from log import logger
import numpy as np
import os
import transformations
from se3_math import log_SO3, exp_SO3, interpolate_SE3, interpolate_SO3, log_SE3
import matplotlib.pyplot as plt
import time
import pandas

if "DISPLAY" not in os.environ:
    plt.switch_backend("Agg")

lat, lon, alt, roll, pitch, yaw, vn, ve, vf, vl, vu, ax, ay, az, af, al, au, wx, wy, wz, wf, wl, wu, \
posacc, velacc, navstat, numsats, posmode, velmode, orimode = list(range(0, 30))


class Frame(object):
    def __init__(self, image_path, timestamp, T_i_vk, T_cam_imu, v_vk_i_vk,
                 imu_poses, imu_timestamps, accel_measurements, gyro_measurements):
        self.image_path = image_path
        self.timestamp = timestamp
        self.T_i_vk = T_i_vk  # inertial to vehicle frame pose
        self.T_cam_imu = T_cam_imu  # calibration from imu to camera
        self.v_vk_i_vk = v_vk_i_vk  # velocity expressed in vehicle frame
        self.imu_timestamps = imu_timestamps
        self.imu_poses = imu_poses
        self.accel_measurements = accel_measurements
        self.gyro_measurements = gyro_measurements

        assert (len(imu_timestamps) == len(accel_measurements))
        assert (len(imu_timestamps) == len(gyro_measurements))
        assert (len(imu_timestamps) == len(imu_poses))


class Plotter(object):
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.counter = 0

    def plot(self, plots, xlabel, ylabel, title, labels=None, equal_axes=False):
        if not labels:
            labels_txt = [None] * len(plots)
        else:
            labels_txt = labels
        assert (len(plots) == len(labels_txt))

        plt.clf()
        for i in range(0, len(plots)):
            plt.plot(*plots[i], label=labels_txt[i])
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.title(title)

        if equal_axes:
            plt.axis("equal")

        if labels:
            plt.legend()

        plt.grid()
        filename = "%02d_%s.png" % (self.counter, "_".join(title.lower().split()))
        plt.savefig(os.path.join(self.output_dir, filename))
        self.counter += 1


def read_timestamps(ts_file):
    f = open(ts_file, "r")
    timestamps = []
    for line in f:
        line = line.strip()
        if line:
            timestamps.append(np.datetime64(line))
    # put the time in local frame of reference, and convert to seconds
    return np.array(timestamps)


def interpolate(imu_data_i, imu_data_j, pose_i, pose_j, alpha):
    # rotation
    C_i_vi = transformations.euler_matrix(imu_data_i[yaw], imu_data_i[pitch], imu_data_i[roll], 'rzyx')[0:3, 0:3]
    C_i_vj = transformations.euler_matrix(imu_data_j[yaw], imu_data_j[pitch], imu_data_j[roll], 'rzyx')[0:3, 0:3]
    C_i_vk = interpolate_SO3(C_i_vi, C_i_vj, alpha)
    C_i_hi = transformations.euler_matrix(imu_data_i[yaw], 0, 0, 'rzyx')[0:3, 0:3]
    C_i_hj = transformations.euler_matrix(imu_data_j[yaw], 0, 0, 'rzyx')[0:3, 0:3]
    C_i_hk = interpolate_SO3(C_i_hi, C_i_hj, alpha)

    # pose
    T_i_vk = interpolate_SE3(pose_i, pose_j, alpha)

    C_vk_vj = C_i_vk.transpose().dot(C_i_vj)
    C_vk_vi = C_i_vk.transpose().dot(C_i_vi)

    # acceleration
    a_vi = np.array([imu_data_i[ax], imu_data_i[ay], imu_data_i[az]])
    a_vj = np.array([imu_data_j[ax], imu_data_j[ay], imu_data_j[az]])
    a_vk = alpha * (C_vk_vj.dot(a_vj) - C_vk_vi.dot(a_vi)) + C_vk_vi.dot(a_vi)

    # angular velocity
    w_vi = np.array([imu_data_i[wx], imu_data_i[wy], imu_data_i[wz]])
    w_vj = np.array([imu_data_j[wx], imu_data_j[wy], imu_data_j[wz]])
    w_vk = alpha * (C_vk_vj.dot(w_vj) - C_vk_vi.dot(w_vi)) + C_vk_vi.dot(w_vi)

    # velocity
    v_hi = np.array([imu_data_i[vf], imu_data_i[vl], imu_data_i[vu]])
    v_hj = np.array([imu_data_j[vf], imu_data_j[vl], imu_data_j[vu]])
    v_hk = alpha * (v_hj - v_hi) + v_hi
    v_vk = C_i_vk.transpose().dot(C_i_hk).dot(v_hk)

    # it seems there is a mismatch between reported velocity and velocity estimated
    # from differentiating poses, they seem to be negative of each other only on the
    # y axis, not sure why
    v_vk[1] = -v_vk[1]

    return T_i_vk, v_vk, w_vk, a_vk


def remove_negative_timesteps(imu_timestamps, imu_data, gps_poses):
    # indices_to_remove = []
    # for i in range(1, len(imu_timestamps) - 1):
    #     dt2 = (imu_timestamps[i + 1] - imu_timestamps[i]) / np.timedelta64(1, 's')
    #     dt1 = (imu_timestamps[i] - imu_timestamps[i - 1]) / np.timedelta64(1, 's')
    #
    #     if dt1 > 0.05 and dt2 < 0:
    #         logger.print("Negative timestep detected idx [%d -> %d -> %d]" % (i - 1, i, i + 1))
    #         indices_to_remove.append(i)
    #
    # imu_timestamps = np.delete(imu_timestamps, indices_to_remove, 0)
    # imu_data = np.delete(imu_data, indices_to_remove, 0)
    # gps_poses = np.delete(gps_poses, indices_to_remove, 0)
    # logger.print("Negative timesteps removed.")

    imu_timestamps, indices = np.unique(imu_timestamps, return_index=True)
    indices_sort = np.argsort(imu_timestamps)
    imu_data = imu_data[indices, :][indices_sort, :]
    gps_poses = gps_poses[indices, :][indices_sort, :]

    # double check
    for i in range(1, len(imu_timestamps)):
        dt = (imu_timestamps[i] - imu_timestamps[i - 1]) / np.timedelta64(1, 's')
        if dt > (1.5 / 100):
            logger.print("WARNING: Larger than usual timestep of %.5fs detected idx [%d -> %d]" % (dt, i - 1, i))
        assert (dt > 0)

    return imu_timestamps, imu_data, gps_poses


def find_timestamps_in_between(timestamp, timestamps_to_search):
    # ensure they are in between
    assert (timestamp >= timestamps_to_search[0])
    assert (timestamp <= timestamps_to_search[-1])

    index = 0
    while timestamps_to_search[index] <= timestamp:
        index += 1
    return index - 1, index


def check_time_discontinuities(raw_seq_dir):
    oxts_dir = os.path.join(raw_seq_dir, "oxts")
    cam_timestamps = read_timestamps(os.path.join(os.path.join(raw_seq_dir, "image_02"), "timestamps.txt"))
    imu_timestamps = read_timestamps(os.path.join(oxts_dir, "timestamps.txt"))

    for i in range(1, len(imu_timestamps)):
        dt = (imu_timestamps[i] - imu_timestamps[i - 1]) / np.timedelta64(1, 's')
        if dt > (1.5 / 100.0):
            print("+ve timestep skip %8.5fs, idx [%6d -> %6d], time [%30s -> %30s]" %
                  (dt, i - 1, i, imu_timestamps[i - 1], imu_timestamps[i]))
        elif dt < (0.5 / 100.0):
            print("-ve timestep skip %8.5fs, idx [%6d -> %6d], time [%30s -> %30s]" %
                  (dt, i - 1, i, imu_timestamps[i - 1], imu_timestamps[i]))


def preprocess_kitti_raw(raw_seq_dir, output_dir, cam_subset_range):
    logger.initialize(working_dir=output_dir, use_tensorboard=False)
    logger.print("================ PREPROCESS KITTI RAW ================")
    logger.print("Preprocessing %s" % raw_seq_dir)
    logger.print("Output to: %s" % output_dir)
    logger.print("Camera images: %d => %d" % (cam_subset_range[0], cam_subset_range[1]))

    oxts_dir = os.path.join(raw_seq_dir, "oxts")
    image_dir = os.path.join(raw_seq_dir, "image_02")
    gps_poses = np.loadtxt(os.path.join(oxts_dir, "poses.txt"))
    gps_poses = np.array([np.vstack([np.reshape(p, [3, 4]), [0, 0, 0, 1]]) for p in gps_poses])
    T_velo_imu = np.loadtxt(os.path.join(raw_seq_dir, "../T_velo_imu.txt"))
    T_cam_velo = np.loadtxt(os.path.join(raw_seq_dir, '../T_cam_velo.txt'))
    T_cam_imu = T_cam_velo.dot(T_velo_imu)

    # load IMU data
    imu_data = []
    imu_data_files = sorted(os.listdir(os.path.join(oxts_dir, "data")))
    start_time = time.time()
    for i in range(0, len(imu_data_files)):
        print("Loading IMU data files %d/%d (%.2f%%)" %
              (i + 1, len(imu_data_files), 100 * (i + 1) / len(imu_data_files)), end='\r')
        imu_data.append(np.loadtxt(os.path.join(oxts_dir, "data", imu_data_files[i])))
    imu_data = np.array(imu_data)
    logger.print("Loading IMU data took %.2fs" % (time.time() - start_time))

    # imu timestamps
    imu_timestamps = read_timestamps(os.path.join(oxts_dir, "timestamps.txt"))
    assert (len(imu_data) == len(gps_poses))
    assert (len(imu_timestamps) == len(gps_poses))
    imu_timestamps, imu_data, gps_poses = remove_negative_timesteps(imu_timestamps, imu_data, gps_poses)

    # load image data
    cam_timestamps = read_timestamps(os.path.join(image_dir, "timestamps.txt"))
    image_paths = sorted(os.listdir(os.path.join(image_dir, "data")))  # image data exists are part of paths
    assert (len(cam_timestamps) == len(image_paths))
    assert (cam_subset_range[0] >= 0 and cam_subset_range[1] < len(image_paths))

    # the first camera timestamps must be between IMU timestamps
    assert (cam_timestamps[cam_subset_range[0]] >= imu_timestamps[0])
    assert (cam_timestamps[cam_subset_range[1]] <= imu_timestamps[-1])
    # convert to local time reference in seconds
    cam_timestamps = (cam_timestamps - imu_timestamps[0]) / np.timedelta64(1, 's')
    imu_timestamps = (imu_timestamps - imu_timestamps[0]) / np.timedelta64(1, 's')

    # take subset of the camera images int the range of images we are interested in
    image_paths = image_paths[cam_subset_range[0]: cam_subset_range[1] + 1]
    cam_timestamps = cam_timestamps[cam_subset_range[0]: cam_subset_range[1] + 1]

    # take a subset of imu data corresponds to camera images
    idx_imu_data_start = find_timestamps_in_between(cam_timestamps[0], imu_timestamps)[0]
    idx_imu_data_end = find_timestamps_in_between(cam_timestamps[-1], imu_timestamps)[1]
    # idx_imu_data_start = 0
    # while imu_timestamps[idx_imu_data_start] < cam_timestamps[0]:
    #     idx_imu_data_start += 1
    # idx_imu_data_start -= 1
    # idx_imu_data_end = 0
    # while imu_timestamps[idx_imu_data_end] < cam_timestamps[-1]:
    #     idx_imu_data_end += 1
    imu_timestamps = imu_timestamps[idx_imu_data_start:idx_imu_data_end + 1]
    imu_data = imu_data[idx_imu_data_start:idx_imu_data_end + 1]
    gps_poses = gps_poses[idx_imu_data_start:idx_imu_data_end + 1]

    idx_imu_slice_start = 0
    idx_imu_slice_end = 0
    data_frames = []
    start_time = time.time()
    for k in range(0, len(cam_timestamps) - 1):
        print("Processing IMU data files %d/%d (%.2f%%)" % (
            k + 1, len(cam_timestamps), 100 * (k + 1) / len(cam_timestamps)), end='\r')

        t_k = cam_timestamps[k]
        t_kp1 = cam_timestamps[k + 1]

        # the start value does not need to be recomputed, since you can get that from the previous time step, but
        # i am a lazy person, this will work
        idx_imu_slice_start = find_timestamps_in_between(t_k, imu_timestamps)[1]

        assert (imu_timestamps[idx_imu_slice_start - 1] <= t_k <= imu_timestamps[idx_imu_slice_start])
        # interpolate
        tk_i = imu_timestamps[idx_imu_slice_start - 1]
        tk_j = imu_timestamps[idx_imu_slice_start]
        alpha_k = (t_k - tk_i) / (tk_j - tk_i)
        T_i_vk, v_vk, w_vk, a_vk = \
            interpolate(imu_data[idx_imu_slice_start - 1], imu_data[idx_imu_slice_start],
                        gps_poses[idx_imu_slice_start - 1], gps_poses[idx_imu_slice_start], alpha_k)

        idx_imu_slice_end = find_timestamps_in_between(t_kp1, imu_timestamps)[1]
        assert (imu_timestamps[idx_imu_slice_end - 1] <= t_kp1 <= imu_timestamps[idx_imu_slice_end])
        # interpolate
        tkp1_i = imu_timestamps[idx_imu_slice_end - 1]
        tkp1_j = imu_timestamps[idx_imu_slice_end]
        alpha_kp1 = (t_kp1 - tkp1_i) / (tkp1_j - tkp1_i)
        T_i_vkp1, v_vkp1, w_vkp1, a_vkp1 = \
            interpolate(imu_data[idx_imu_slice_end - 1], imu_data[idx_imu_slice_end],
                        gps_poses[idx_imu_slice_end - 1], gps_poses[idx_imu_slice_end], alpha_kp1)

        imu_timestamps_k_kp1 = np.concatenate(
                [[t_k], imu_timestamps[idx_imu_slice_start:idx_imu_slice_end - 1], [t_kp1]])
        imu_poses = np.concatenate([[T_i_vk], gps_poses[idx_imu_slice_start:idx_imu_slice_end - 1], [T_i_vkp1]])
        accel_measurements_k_kp1 = np.concatenate([[a_vk],
                                                   imu_data[idx_imu_slice_start: idx_imu_slice_end - 1, ax:az + 1],
                                                   [a_vkp1]])
        gyro_measurements_k_kp1 = np.concatenate([[w_vk],
                                                  imu_data[idx_imu_slice_start: idx_imu_slice_end - 1, wx:wz + 1],
                                                  [w_vkp1]])
        frame_k = Frame(image_paths[k], t_k, T_i_vk, T_cam_imu, v_vk,
                        imu_poses, imu_timestamps_k_kp1, accel_measurements_k_kp1, gyro_measurements_k_kp1)
        data_frames.append(frame_k)

        # assertions for sanity check
        assert (np.allclose(data_frames[-1].timestamp, data_frames[-1].imu_timestamps[0], atol=1e-13))
        assert (np.allclose(data_frames[-1].T_i_vk, data_frames[-1].imu_poses[0], atol=1e-13))
        if len(data_frames) > 1:
            assert (np.allclose(data_frames[-1].timestamp, data_frames[-2].imu_timestamps[-1], atol=1e-13))
            assert (np.allclose(data_frames[-1].T_i_vk, data_frames[-2].imu_poses[-1], atol=1e-13))
            assert (
                np.allclose(data_frames[-1].accel_measurements[0], data_frames[-2].accel_measurements[-1], atol=1e-13))
            assert (
                np.allclose(data_frames[-1].accel_measurements[0], data_frames[-2].accel_measurements[-1], atol=1e-13))

    # add the last frame without any IMU data
    data_frames.append(Frame(image_paths[-1], t_kp1, T_i_vkp1, T_cam_imu, v_vkp1,
                             np.zeros([0, 4, 4]), np.zeros([0]), np.zeros([0, 3]), np.zeros([0, 3])))

    logger.print("Processing data took %.2fs" % (time.time() - start_time))

    start_time = time.time()
    data = {"image_path": [f.image_path for f in data_frames],
            "timestamp": [f.timestamp for f in data_frames],
            "T_i_vk": [f.T_i_vk for f in data_frames],
            "T_cam_imu": [f.T_cam_imu for f in data_frames],
            "v_vk_i_vk": [f.v_vk_i_vk for f in data_frames],
            "imu_timestamps": [f.imu_timestamps for f in data_frames],
            "imu_poses": [f.imu_poses for f in data_frames],
            "accel_measurements": [f.accel_measurements for f in data_frames],
            "gyro_measurements": [f.gyro_measurements for f in data_frames]}
    pandas_df = pandas.DataFrame(data, columns=data.keys())
    pandas_df.to_pickle(os.path.join(output_dir, "data.pickle"))
    logger.print("Saving pandas took %.2fs" % (time.time() - start_time))

    # ============================== FIGURES FOR SANITY TESTS ==============================
    # plot trajectory
    start_time = time.time()
    plotter = Plotter(output_dir)
    p_poses = np.array(data["T_i_vk"])
    p_timestamps = np.array(data["timestamp"])
    p_velocities = np.array(data["v_vk_i_vk"])

    p_imu_timestamps = np.concatenate([d[:-1] for d in data['imu_timestamps']])
    p_gyro_measurements = np.concatenate([d[:-1] for d in data['gyro_measurements']])
    p_accel_measurements = np.concatenate([d[:-1] for d in data["accel_measurements"]])
    p_imu_poses = np.concatenate([d[:-1, :, :] for d in data["imu_poses"]])
    assert (len(p_imu_timestamps) == len(p_gyro_measurements))
    assert (len(p_imu_timestamps) == len(p_accel_measurements))
    assert (len(p_imu_timestamps) == len(p_imu_poses))

    # integrate accel to compare against velocity
    p_accel_int = [p_velocities[0, :]]
    p_accel_int_int = [p_poses[0, :3, 3]]
    # g = np.array([0, 0, 9.80665])
    g = np.array([0, 0, 9.808679801065017])
    # g = np.array([0, 0, 9.8096])
    for i in range(0, len(p_imu_timestamps) - 1):
        dt = p_imu_timestamps[i + 1] - p_imu_timestamps[i]
        C_i_vk = p_imu_poses[i, :3, :3]
        C_vkp1_vk = p_imu_poses[i + 1, :3, :3].transpose().dot(p_imu_poses[i, :3, :3])

        v_vk_i_vk = p_accel_int[-1]
        v_vkp1_vk_vk = dt * (p_accel_measurements[i] - C_i_vk.transpose().dot(g))
        v_vkp1_i_vk = v_vk_i_vk + v_vkp1_vk_vk
        p_accel_int.append(C_vkp1_vk.dot(v_vkp1_i_vk))
        p_accel_int_int.append(p_accel_int_int[-1] + p_imu_poses[i, :3, :3].dot(p_accel_int[-1]) * dt)
    p_accel_int = np.array(p_accel_int)
    p_accel_int_int = np.array(p_accel_int_int)

    # poses from integrating velocity
    p_vel_int_poses = [p_poses[0, :3, 3]]
    for i in range(0, len(p_velocities) - 1):
        dt = p_timestamps[i + 1] - p_timestamps[i]
        dp = p_poses[i, :3, :3].dot(p_velocities[i]) * dt
        p_vel_int_poses.append(p_vel_int_poses[-1] + dp)
    p_vel_int_poses = np.array(p_vel_int_poses)

    plotter.plot(([p_poses[:, 0, 3], p_poses[:, 1, 3]],
                  [p_vel_int_poses[:, 0], p_vel_int_poses[:, 1]],
                  [p_accel_int_int[:, 0], p_accel_int_int[:, 1]],),
                 "x [m]", "Y [m]", "XY Plot", labels=["dat_poses", "dat_vel_int", "dat_acc_int^2"], equal_axes=True)
    plotter.plot(([p_poses[:, 0, 3], p_poses[:, 2, 3]],
                  [p_vel_int_poses[:, 0], p_vel_int_poses[:, 2]],
                  [p_accel_int_int[:, 0], p_accel_int_int[:, 2]],),
                 "X [m]", "Z [m]", "XZ Plot", labels=["dat_poses", "dat_vel_int", "dat_acc_int^2"], equal_axes=True)
    plotter.plot(([p_poses[:, 1, 3], p_poses[:, 2, 3]],
                  [p_vel_int_poses[:, 1], p_vel_int_poses[:, 2]],
                  [p_accel_int_int[:, 1], p_accel_int_int[:, 2]],),
                 "Y [m]", "Z [m]", "YZ Plot", labels=["dat_poses", "dat_vel_int", "dat_acc_int^2"], equal_axes=True)

    plotter.plot(([p_timestamps, p_poses[:, 0, 3]],
                  [p_timestamps, p_vel_int_poses[:, 0]],
                  [p_imu_timestamps, p_accel_int_int[:, 0]],),
                 "t [s]", "Y [m]", "X Plot From Zero", labels=["dat_poses", "dat_vel_int", "dat_acc_int^2"])
    plotter.plot(([p_timestamps, p_poses[:, 1, 3]],
                  [p_timestamps, p_vel_int_poses[:, 1]],
                  [p_imu_timestamps, p_accel_int_int[:, 1]],),
                 "t [s]", "Z [m]", "Y Plot From Zero", labels=["dat_poses", "dat_vel_int", "dat_acc_int^2"])
    plotter.plot(([p_timestamps, p_poses[:, 2, 3]],
                  [p_timestamps, p_vel_int_poses[:, 2]],
                  [p_imu_timestamps, p_accel_int_int[:, 2]],),
                 "t [s]", "Z [m]", "Z Plot From Zero", labels=["dat_poses", "dat_vel_int", "dat_acc_int^2"])

    # plot trajectory rotated wrt to the first frame
    p_poses_from_I = np.array([np.linalg.inv(p_poses[0]).dot(p) for p in p_poses])
    plotter.plot(([p_poses_from_I[:, 0, 3], p_poses_from_I[:, 1, 3]],),
                 "x [m]", "Y [m]", "XY Plot From Identity", equal_axes=True)
    plotter.plot(([p_poses_from_I[:, 0, 3], p_poses_from_I[:, 2, 3]],),
                 "X [m]", "Z [m]", "XZ Plot From Identity", equal_axes=True)
    plotter.plot(([p_poses_from_I[:, 1, 3], p_poses_from_I[:, 2, 3]],),
                 "Y [m]", "Z [m]", "YZ Plot From Identity", equal_axes=True)

    # plot p_velocities
    plotter.plot(([p_timestamps, p_velocities[:, 0]], [p_timestamps, p_velocities[:, 1]],
                  [p_timestamps, p_velocities[:, 2]]), "t [s]", "v [m/s]", "YZ Plot",
                 labels=["dat_vx", "dat_vy", "dat_vz"])

    # make sure the interpolated acceleration and gyroscope measurements are the same
    plotter.plot(([p_imu_timestamps, p_gyro_measurements[:, 0]], [imu_timestamps, imu_data[:, wx]],),
                 "t [s]", "w [rad/s]", "Rot Vel X Verification")
    plotter.plot(([p_imu_timestamps, p_gyro_measurements[:, 1]], [imu_timestamps, imu_data[:, wy]],),
                 "t [s]", "w [rad/s]", "Rot Vel Y Verification")
    plotter.plot(([p_imu_timestamps, p_gyro_measurements[:, 2]], [imu_timestamps, imu_data[:, wz]],),
                 "t [s]", "w [rad/s]", "Rot Vel Z Verification")
    plotter.plot(([p_imu_timestamps, p_accel_measurements[:, 0]], [imu_timestamps, imu_data[:, ax]],),
                 "t [s]", "a [m/s^2]", "Accel X Verification")
    plotter.plot(([p_imu_timestamps, p_accel_measurements[:, 1]], [imu_timestamps, imu_data[:, ay]],),
                 "t [s]", "a [m/s^2]", "Accel Y Verification")
    plotter.plot(([p_imu_timestamps, p_accel_measurements[:, 2]], [imu_timestamps, imu_data[:, az]],),
                 "t [s]", "a [m/s^2]", "Accel Z Verification")

    # integrate gyroscope to compare against rotation
    p_gyro_int = [data["T_i_vk"][0][:3, :3]]
    for i in range(0, len(p_imu_timestamps) - 1):
        dt = p_imu_timestamps[i + 1] - p_imu_timestamps[i]
        p_gyro_int.append(p_gyro_int[-1].dot(exp_SO3(dt * p_gyro_measurements[i])))
    p_gyro_int = np.array([log_SO3(o) for o in p_gyro_int])
    p_orientation = np.array([log_SO3(p[:3, :3]) for p in data["T_i_vk"]])

    plotter.plot(([p_imu_timestamps, np.unwrap(p_gyro_int[:, 0])], [p_timestamps, np.unwrap(p_orientation[:, 0])],),
                 "t [s]", "rot [rad/s]", "Theta X Cmp Plot", labels=["gyro_int", "dat_pose"])
    plotter.plot(([p_imu_timestamps, np.unwrap(p_gyro_int[:, 1])], [p_timestamps, np.unwrap(p_orientation[:, 1])],),
                 "t [s]", "rot [rad/s]", "Theta Y Cmp Plot", labels=["gyro_int", "dat_pose"])
    plotter.plot(([p_imu_timestamps, np.unwrap(p_gyro_int[:, 2])], [p_timestamps, np.unwrap(p_orientation[:, 2])],),
                 "t [s]", "rot [rad/s]", "Theta Z Cmp Plot", labels=["gyro_int", "dat_pose"])

    vel_from_gps_rel_poses = []
    for k in range(0, len(gps_poses) - 1):
        dt = imu_timestamps[k + 1] - imu_timestamps[k]
        T_i_vk = gps_poses[k]
        T_i_vkp1 = gps_poses[k + 1]
        T_vk_vkp1 = np.linalg.inv(T_i_vk).dot(T_i_vkp1)
        vel_from_gps_rel_poses.append(T_vk_vkp1[0:3, 3] / dt)
        # vel_from_gps_rel_poses.append(log_SE3(T_vk_vkp1)[0:3] / dt)
    vel_from_gps_rel_poses = np.array(vel_from_gps_rel_poses)

    plotter.plot(([imu_timestamps[1:], vel_from_gps_rel_poses[:, 0]],
                  [p_timestamps, p_velocities[:, 0]],
                  [p_imu_timestamps, p_accel_int[:, 0]],),
                 "t [s]", "v [m/s]", "Velocity X Cmp Plot", labels=["gps_rel", "dat_vel", "dat_accel_int"])
    plotter.plot(([imu_timestamps[1:], vel_from_gps_rel_poses[:, 1]],
                  [p_timestamps, p_velocities[:, 1]],
                  [p_imu_timestamps, p_accel_int[:, 1]],),
                 "t [s]", "v [m/s]", "Velocity Y Cmp Plot", labels=["gps_rel", "dat_vel", "dat_accel_int"])
    plotter.plot(([imu_timestamps[1:], vel_from_gps_rel_poses[:, 2]],
                  [p_timestamps, p_velocities[:, 2]],
                  [p_imu_timestamps, p_accel_int[:, 2]],),
                 "t [s]", "v [m/s]", "Velocity Z Cmp Plot", labels=["gps_rel", "dat_vel", "dat_accel_int"])

    logger.print("Generating figures took %.2fs" % (time.time() - start_time))
    logger.print("All done!")