import numpy as np

def detect_nod(df, min_amplitude=50):
    """
    Detects a nod based on a significant up and down movement in yaw.
    Ensures that the yaw returns toward the other extreme.
    """
    yaw = df["yaw"].values
    if len(yaw) < 3:
        return False

    yaw_max = np.max(yaw)
    yaw_min = np.min(yaw)
    delta = yaw_max - yaw_min

    if delta < min_amplitude:
        return False

    # Confirm that the movement returned toward the opposite extreme
    latest_yaw = yaw[-1]
    returned_to_min = abs(latest_yaw - yaw_min) < min_amplitude * 0.5
    returned_to_max = abs(latest_yaw - yaw_max) < min_amplitude * 0.5

    if returned_to_min or returned_to_max:
        return True

    return False
