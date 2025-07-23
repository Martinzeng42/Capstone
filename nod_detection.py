import numpy as np

def detect_nod(df, min_amplitude):
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
    returned_to_min =  yaw_max - latest_yaw > min_amplitude

    if returned_to_min:
        breakpoint()
        return True

    return False
