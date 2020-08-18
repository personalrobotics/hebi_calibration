from __future__ import print_function

import numpy as np
import pandas as pd
import scipy.optimize
from scipy.spatial.transform import Rotation as scipyR
from functools import partial

# ==============================================================
# Utilities for cost function
# ==============================================================

def get_DH_transformation(alpha,a,_theta,d,theta_offset=0):
  theta = _theta + theta_offset
  # Given the DH link construct the transformation matrix
  rot = np.array([[np.cos(theta), -np.sin(theta), 0],
                 [np.sin(theta)*np.cos(alpha), np.cos(theta)*np.cos(alpha), -np.sin(alpha)],
                 [np.sin(theta)*np.sin(alpha), np.cos(theta)*np.sin(alpha), np.cos(alpha)]])

  trans = np.array([a,-d*np.sin(alpha),np.cos(alpha)*d]).reshape(3,1)
  last_row = np.array([[0, 0, 0, 1]])
  m = np.vstack((np.hstack((rot, trans)),last_row))
  return m

def get_transformation_matrix(params):
  # Given 7 params containing quat (x,y,z,w) and shift (x,y,z) return transformation matrix
  qx,qy,qz,qw,x,y,z = params
  rot = scipyR.from_quat((qx, qy, qz, qw)).as_dcm() # scipy >=1.4.0 will always normalize quat
  trans = np.array([x,y,z]).reshape(3,1)
  last_row = np.array([[0, 0, 0, 1]])
  return np.vstack((np.hstack((rot, trans)),last_row))

def calculate_FK_transformation(FKparams, joint_position):
  # Given a list of FKparams, shape N by 3, return transformation
  ee = np.eye(4)
  for (alpha, a, d, offset), theta in zip(FKparams, joint_position):
    ee = ee.dot(get_DH_transformation(alpha, a, theta, d, offset))
  return ee

def get_hebi_fk(joint_positions, arm_hrdf):
  from hebi_env.arm_container import create_empty_robot
  arm = create_empty_robot(arm_hrdf)
  return np.array([np.array(arm.get_FK_ee(p)) for p in joint_positions]) # data_size x 4 x 4

def get_hebi_fk_tips(list_of_hebiee):
  tips = []
  for hebiee in list_of_hebiee:
    x_axis,y_axis,z_axis = hebiee[0:3,0:3].T
    init_position = np.array(hebiee[0:3,3]).reshape(3)
    # update the orignial point from hole to the axis of bottom chop
    position = (init_position + 0 * x_axis +
                 (0.007 - 0.0001) * y_axis + # 0.007 - 0.0001 is about the shift from holder screw to center of chop
                 (0.0017 + 0.0065) * z_axis) # axis coming out from the module plate, 0.0017 holder offset to module plate, 0.0065 bring to chopsticks center
    position = position + x_axis * (0.0035+0.11) # Tip of bottom chopsticks on robot, 0.0035 is half of the holder width, 0.1135 is the first part lengh of the chopsticks
    tips.append(position)
  return tips

def get_m6_in_hebi_frame(list_m6, R_params):
  R = get_transformation_matrix(R_params)
  list_m6_in_hebi_frame = []
  _m6 = np.ones(4)
  for m6 in list_m6:
    _m6[0:3] = m6
    list_m6_in_hebi_frame.append(R.dot(_m6)[0:3].reshape(3))
  return list_m6_in_hebi_frame

def get_fk_tips(list_jp, FK_params):
  DH_params = np.reshape(FK_params[:24], (6,4)) # each link is represented by 4 params
  last_transformation = get_transformation_matrix(FK_params[-7:])
  list_fk_tips = []
  for jp in list_jp:
    ee = calculate_FK_transformation(DH_params, jp)
    ee = ee.dot(last_transformation)
    list_fk_tips.append(ee[0:3, 3])
  return np.array(list_fk_tips).reshape(-1,3)

# ==============================================================
# Optimization cost and initial params
# ==============================================================

measured_R = np.array([0, 0, 0, 1, # quat x y z w, almost identity
     0,0,0])

# 2020.08.13. yield from fit_R.py
measured_R = np.array([0.00795607, 0.00529487, 0.01466389, 0.99984681, -1.07679705, 0.08733636, -0.02163])

measured_FK = np.array([
     # link twist (alpha); link length (a);  joint offset (d); theta_offset;
     0,       0,        0.101,  0, # 0  x  2  3
     np.pi/2, 0,        0.0826, 0, # 4  x  6  7
     np.pi,   0.3255,   0.0451, 0, # 8  9  10 11
     np.pi,   0.3255,   0.0713, 0, # 12 13 14 15
     np.pi/2, 0,        0.1143, 0, # 16 x  18 19
     np.pi/2, 0,        0.1143, 0, # 20 x  22 23
     -0.707,  0,   0, 0.707, 0.1345, 0.0803, 0.025]) # x x x x 28 29 30 # from end to DH to tip

optimized_FK=None

def optimize_R_using_hebi_FK(list_m6, list_tip, initP=None):
  if initP is None:
    initP = np.array(measured_R)

  def cost_func(p, verbose=False):
    loss = []
    p[0:4] = measured_R[0:4] # FIX THE R ROTATION
    R = get_transformation_matrix(p)
    _m6 = np.ones(4)
    for m6, hebi_tip in zip(list_m6, list_tip):
      _m6[0:3] = m6
      transform = R.dot(_m6)[0:3]
      loss.append(np.linalg.norm((transform - hebi_tip).reshape(3))) # Euclidean norm
    return np.average(loss) if not verbose else loss
  return initP, cost_func

def optimize_FK_only(list_m6_in_hebi_frame, list_jp, initP=None, sel_params=np.arange(31)):
  if initP is not None:
    defaultP = np.array(initP)
  else:
    defaultP = np.array(measured_FK)
  initP = defaultP[sel_params]
  def cost_func(_p, verbose=False):
    loss = []
    p = np.array(defaultP)
    p[sel_params] = _p
    #p[8] += p[11] - measured_FK[11] + p[5] - measured_FK[5] ####### Consider add this constraint
    DH_params = p[:24].reshape(6,4)
    last_transformation = get_transformation_matrix(p[-7:])
    for m6, cp in zip(list_m6_in_hebi_frame, list_jp):
      ee = calculate_FK_transformation(DH_params, cp)
      ee = ee.dot(last_transformation)
      prediction = ee[0:3, 3].reshape(3)
      loss.append(np.linalg.norm(prediction - m6))
    # punish the deviation
    deviation_loss = np.exp(np.abs(p - measured_FK) * 10) - 1
    deviation_loss[2] = 0 # don't punish the joint offset on the base which determines the height
    # TODO consider punish the theta_offset more heavily
    deviation_loss = np.sum(deviation_loss) / (len(sel_params) - 1) / 40
    #print(deviation_loss, np.average(loss))
    return np.average(loss) + deviation_loss if not verbose else loss
  return initP, cost_func

def FK_cost_fn_parallel(list_m6_in_hebi_frame, list_jp, DH_params, last_transformation, idx):
  m6 = list_m6_in_hebi_frame[idx]
  cp = list_jp[idx]
  ee = calculate_FK_transformation(DH_params, cp)
  ee = ee.dot(last_transformation)
  prediction = ee[0:3, 3].reshape(3)
  return np.linalg.norm(prediction - m6)

def optimize_FK_and_R(initRparam, initFKparam, list_m6, list_jp):
  initP = np.hstack((initRparam, initFKparam)).reshape(-1)
  def cost_func(p, verbose=True):
    loss = []
    R_params = np.reshape(p[:7], -1)
    R = get_transformation_matrix(R_params)
    pad_m6 = np.ones((len(list_m6),4))
    pad_m6[:,0:3] = np.array(list_m6)
    DH_params = np.reshape(p[7:25], (6,3))
    last_transformation = get_transformation_matrix(p[-7:])
    for m6, cp in zip(pad_m6, list_jp):
      ee = calculate_FK_transformation(DH_params, cp)
      ee = ee.dot(last_transformation)
      prediction = ee[0:3, 3].reshape(3)
      loss.append(np.linalg.norm(R.dot(m6)[0:3] - prediction))
    return np.average(loss) if not verbose else loss
  return initP, cost_func

from multiprocessing import Pool
from functools import partial

def optimize_FK_only_parallel(list_m6_in_hebi_frame, list_jp, initP=None, sel_params=np.arange(31)):
  if initP is not None:
    defaultP = np.array(initP)
  else:
    defaultP = np.array(measured_FK)
  initP = defaultP[sel_params]

  def cost_func(_p, verbose=False):
    loss = []
    p = np.array(defaultP)
    p[sel_params] = _p
    #p[8] += p[11] - measured_FK[11] + p[5] - measured_FK[5] ####### Consider add this constraint
    DH_params = p[:24].reshape(6,4)
    last_transformation = get_transformation_matrix(p[-7:])

    pool = Pool(5)
    my_func = partial(FK_cost_fn_parallel, list_m6_in_hebi_frame, list_jp, DH_params, last_transformation)
    loss = pool.map(my_func, range(len(list_m6_in_hebi_frame)))
    loss = np.array(loss)

    # punish the deviation
    deviation_loss = np.exp(np.abs(p - measured_FK) * 10) - 1
    deviation_loss[2] = 0 # don't punish the joint offset on the base which determines the height
    # TODO consider punish the theta_offset more heavily
    deviation_loss = np.sum(deviation_loss) / (len(sel_params) - 1) / 40
    #print(deviation_loss, np.average(loss))
    return np.average(loss) + deviation_loss if not verbose else loss
  return initP, cost_func

# ==============================================================
# Optimizer
# ==============================================================

def cmaes(func, initP, var=1):
  import cma
  es = cma.CMAEvolutionStrategy(initP, var)
  best_so_far = func(initP)
  best_params = initP
  while not es.stop():
    solutions = es.ask()
    f_vals = [func(s) for s in solutions]
    es.tell(solutions, f_vals)
    if np.min(f_vals) < best_so_far:
      best_so_far = np.min(f_vals)
      best_params = solutions[np.argmin(f_vals)]
      print('CMAES found a new set of best params, achieving', best_so_far)
      print('params', best_params)
    es.logger.add()
    es.disp()
  es.result_pretty()
  return best_params

def scipy_optimize(func, initP, method='BFGS', max_func=15000, iprint=1, save=None):
  # Run scipy optimization
  res = scipy.optimize.minimize(func, initP, method=method, options={'disp': None, 'maxfun': max_func, 'iprint': iprint})
  # For more options see https://docs.scipy.org/doc/scipy/reference/optimize.minimize-lbfgsb.html#optimize-minimize-lbfgsb
  print('After optimize, minimum=', func(res.x))
  print("Scipy optimized params", res.x)
  (save and np.savetxt('results/'+save, res.x, delimiter=',',fmt='%f'))
  return res

if __name__ == '__main__':
  # Load data from CSV that contains m6 (optitrack tip location) and jp (joint positions)
  df = pd.read_csv('data/m6_jps.csv')
  list_m6 = [np.fromstring(r[1:-1], dtype=np.float, sep=' ') for r in df['m6'].to_list()] #[1:-1] to exclude '['']'
  list_jp = [np.fromstring(r[1:-1], dtype=np.float, sep=' ')[0:6]  for r in df['joint_position'].to_list()] # keep only 6 joints
  print("size of datapoints:", len(list_m6))
  print("first m6", list_m6[0])
  print("first jp", list_jp[0])

  # Extract Hebi Default FK EE
  list_hebiee = get_hebi_fk(list_jp, arm_hrdf='/home/hebi/hebi/hebi_ws/src/hebi_teleop/gains/chopstick7D.hrdf')
  # expecting hebiee to be at where the chopstick holder touch the bottom plate, should be defined in arm_container
  list_hebiee_tip = get_hebi_fk_tips(list_hebiee)
  print("first Hebi EE\n", list_hebiee[0])
  print("first Hebi calculated tip\n", list_hebiee_tip[0])

  # dummy params
  R_params, _ = optimize_R_using_hebi_FK(None, None)
  FK_params, _ = optimize_FK_only(None, None)

  initP, cost_func = optimize_R_using_hebi_FK(list_m6, list_hebiee_tip)
  init_distance = cost_func(initP, verbose=True)
  print('Using HEBI default FK ...')
  print('Before optimize, avg distance =', np.average(init_distance))
  print('Before optimize, max distance = ', np.max(init_distance))
  print('Before optimize, the worst datapoint is ', list_m6[np.argmax(init_distance)], list_hebiee_tip[np.argmax(init_distance)])
  # print('All distance\n')
  # print(init_distance)
  # ----------------------------------------------------------------------------
  # STEP1: Optimize R
  # ----------------------------------------------------------------------------
  if False:
    print("\n\nOptimize the transformation matrix R from optitrack frame to hebi\n\n")
    # scipy optimize
    res = scipy_optimize(cost_func, initP, method='L-BFGS-B', max_func=1000, iprint=10).x
    est_R = res
    est_R[0:4] = np.array(est_R[0:4]) / np.linalg.norm(est_R[0:4]) # normalize quat
    print("Estimated R from optitrack to base", est_R)
    print("Compared with initial P:", initP)
    newCost = cost_func(res, verbose=True)
    print('After optimize, avg distance =', np.average(newCost))
    print('After optimize, max distance = ', np.max(newCost))
    # cmaes optimize
    # res = cmaes(cost_func, initP)
    # res[0:4] = np.array(res[0:4]) / np.linalg.norm(res)
    # print("CMEAS (perhaps more of a global optim)", res)
    R_params = res

  # ----------------------------------------------------------------------------
  # STEP2: Optimize Hebi FK
  # ----------------------------------------------------------------------------
  if True:
    print("\n\nOptimize FK function\n\n")
    def opt_fk(sel_params):
      print("Optimizing select parameters for FK, sel:", sel_params)
      list_m6_in_hebi_frame = get_m6_in_hebi_frame(list_m6, R_params)
      initP, cost_func = optimize_FK_only(list_m6_in_hebi_frame, list_jp, initP=FK_params, sel_params=sel_params)

      # from timeit import default_timer as timer
      # start_t = timer()
      # for _ in range(4):
      #   initLoss = cost_func(initP, verbose=True)
      # print(timer() - start_t)
      # return

      print('Before optimize, avg distance =', np.average(initLoss))
      print('Before optimize, max distance = ', np.max(initLoss))
      res = scipy_optimize(cost_func, initP, method='L-BFGS-B', max_func=2000, iprint=20).x
      # res =optimized_FK[sel_params]# used to find the outlier
      newCost = cost_func(res, verbose=True)
      print('After optimize, avg distance =', np.average(newCost))
      print('After optimize, max distance = ', np.max(newCost))

      ## find the outlier
      # idx = (-np.array(cost_optimized)).argsort()[:1000]
      # cost_optimized=np.asarray(newCost)

      # _idx=np.where(cost_optimized>=0.008)[0]
      # print('the index of largest errors comes from these trials:',_idx)
      # print('the number of them',_idx.shape[0])
      # idx=np.sort(_idx)
      # print('sorted index:',_idx)
      # prev=_idx[0]
      # exist_sencond_trial=False
      # for _v in _idx[1:]:
      #   if (_v-prev) != 1:
      #     print("this could be a new trial",_v)
      #     exist_sencond_trial=True
      #   prev=_v
      # if not exist_sencond_trial:
      #   print("this is only one trial")

      import seaborn as sns
      import matplotlib.pyplot as plt
      sns.distplot(newCost)
      plt.show()
      x = np.arange(len(newCost))
      sns.jointplot(x=x, y=newCost)
      plt.show()
      return res

    a = [0, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 23, 28, 29, 30] # optimize selective set
    b = [0, 2, 3, 4, 6, 8, 9, 10, 12, 13, 14, 16, 18, 20, 22, 28, 29, 30] # optimize selective set

    for opt_params in [b]:
      new_FK_params = opt_fk(opt_params)
      FK_params[opt_params] = new_FK_params

      ##force the p[8] follow such rule if you add the constraint in cost fn
      #FK_params[8] += FK_params[5] - measured_FK[5] + FK_params[11] - measured_FK[11]

    np.set_printoptions(suppress=True, formatter={'float_kind':'{:.20f},'.format}, linewidth=80)
    print(FK_params)
    print("Changes")
    print(FK_params - measured_FK)
    optimized_FK = FK_params


  FK_params = optimized_FK

  # ----------------------------------------------------------------------------
  # STEP3: Optimize R and FK iteratively
  # ----------------------------------------------------------------------------
  if False:
      FK_params = optimized_FK
      print("\n\nOptimize R and FK iteratively")
      initP, cost_func = optimize_FK_and_R(R_params, FK_params, list_m6, list_jp)
      initCost = cost_func(initP, verbose=True)
      print("Before optimization, avg distance", np.average(initCost))
      print("Max distance", np.max(initCost))
      for _ in range(1):
        list_fk_tips = get_fk_tips(list_jp, FK_params)
        _, cost_func = optimize_R_using_hebi_FK(list_m6, list_fk_tips)
        R_params = scipy_optimize(cost_func, R_params, method='L-BFGS-B', max_func=1000, iprint=50).x
        R_params[0:4] = R_params[:4] / np.linalg.norm(R_params[:4])
        print('New R params', R_params)
        newCost = cost_func(R_params, verbose=True)
        print("New average distance", np.average(newCost))
        print("Max distance", np.max(newCost))
        print('\n\n')
        exit()
        list_m6_in_hebi_frame = get_m6_in_hebi_frame(list_m6, R_params)
        _, cost_func = optimize_FK_only(list_m6_in_hebi_frame, list_jp)
        FK_params = scipy_optimize(cost_func, FK_params, method='L-BFGS-B', max_func=1000, iprint=50).x
        newP, cost_func = optimize_FK_and_R(R_params, FK_params, list_m6, list_jp)
        newCost = cost_func(newP, verbose=True)
        print("New average distance", np.average(newCost))
        print("Max distance", np.max(newCost))

  # ----------------------------------------------------------------------------
  # ??? STEP4: Optimize R and FK jointly
  # ----------------------------------------------------------------------------
  if False:
    print("\n\nJointly optimize R and FK\n\n")
    initP, cost_func = optimize_FK_and_R(R_params, FK_params, list_m6, list_jp)
    print('Before optimize, avg distance =', cost_func(initP))
    res = scipy_optimize(cost_func, initP, method='L-BFGS-B', max_func=30000, iprint=50).x
    print('Optimized distance', cost_func(res))
