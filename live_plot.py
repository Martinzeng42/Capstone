import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets, QtCore
import pandas as pd
from pathlib import Path
import sys
import traceback

CSV_FILE = Path("logs/csv/sensor_data.csv")

app = QtWidgets.QApplication(sys.argv)
win = pg.GraphicsLayoutWidget(title="SensorTile Live Head Pose")
win.resize(1000, 600)

# Create plots
yaw_plot = win.addPlot(title="Yaw")
yaw_curve = yaw_plot.plot(pen=pg.mkPen('y', width=2))
win.nextRow()

pitch_plot = win.addPlot(title="Pitch")
pitch_curve = pitch_plot.plot(pen=pg.mkPen('r', width=2))
win.nextRow()

roll_plot = win.addPlot(title="Roll")
roll_curve = roll_plot.plot(pen=pg.mkPen('b', width=2))

def update():
    try:
        df = pd.read_csv(CSV_FILE)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        df = df.sort_values("timestamp")

        # Keep last 30 seconds of data
        latest = df["timestamp"].max()
        df = df[df["timestamp"] > (latest - pd.Timedelta(seconds=30))]

        if not df.empty:
            # Convert timestamps to seconds elapsed from earliest timestamp
            time_seconds = (df["timestamp"] - df["timestamp"].min()).dt.total_seconds()
            yaw_curve.setData(time_seconds.values, df["yaw"].values)
            pitch_curve.setData(time_seconds.values, df["pitch"].values)
            roll_curve.setData(time_seconds.values, df["roll"].values)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        traceback.print_exc()

# Update every 100ms
timer = QtCore.QTimer()
timer.timeout.connect(update)
timer.start(100)

win.show()
sys.exit(app.exec_())
