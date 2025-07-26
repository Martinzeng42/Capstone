import numpy as np

def detect_nod(df, min_amplitude):
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
    returned_to_min =  pitch_max - latest_pitch > min_amplitude

    if returned_to_min:
        return True

    return False
