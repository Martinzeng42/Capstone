import numpy as np

def detect_nod_up(df, min_amplitude):
    """
    Detects a nod based on a significant up and down movement in pitch.
    Ensures that the pitch returns toward the other extreme.
    """
    pitch = df["pitch"].values
    if len(pitch) < 3:
        return False

    pitch_max = np.max(pitch)
    pitch_min = np.min(pitch)
    delta = pitch_max - pitch_min

    if delta < min_amplitude:
        return False

    # Confirm that the movement returned toward the opposite extreme
    latest_pitch = pitch[-1]

    if pitch_max - latest_pitch > min_amplitude:
        return True

    return False


def detect_nod_down(df, min_amplitude):
    """
    Detects a nod based on a significant down and up movement in pitch.
    Ensures that the pitch returns toward the other extreme.
    """
    pitch = df["pitch"].values
    if len(pitch) < 3:
        return False

    pitch_max = np.max(pitch)
    pitch_min = np.min(pitch)
    delta = pitch_max - pitch_min

    if delta < min_amplitude:
        return False

    # Confirm that the movement returned toward the opposite extreme
    latest_pitch = pitch[-1]

    if latest_pitch - pitch_min > min_amplitude:
        return True

    return False
