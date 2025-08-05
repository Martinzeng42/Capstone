#include <Arduino.h>
#include <NimBLEDevice.h>
#include <cmath>

// Pin definitions
#define LED_PIN    2    // Onboard flash LED on ESP32-CAM
#define BUTTON_PIN 15   // Push-button for calibration

// SensorTile BLE service & characteristic UUIDs
static BLEUUID serviceUUID("00000000-0004-11e1-9ab4-0002a5d5c51b");
static BLEUUID charUUID   ("00000001-0004-11e1-ac36-0002a5d5c51b");

// Gesture thresholds
const float NOD_THRESHOLD   = 15.0;
const float SHAKE_THRESHOLD = 15.0;

// Baseline & flags
float baselineYaw   = 0.0;
float baselinePitch = 0.0;
bool selecting      = true;
bool yawNeutral     = true;
bool pitchNeutral   = true;

// BLE client & characteristic pointer
NimBLEClient*               pClient     = nullptr;
NimBLERemoteCharacteristic* pRemoteChar = nullptr;

// Notification callback: parse headpose and control LED
void notifyCallback(NimBLERemoteCharacteristic* chr, uint8_t* data, size_t length, bool isNotify) {
  if (length < 21) return;
  float yaw, pitch, roll;
  memcpy(&yaw,   data + 9,  sizeof(float));
  memcpy(&pitch, data + 13, sizeof(float));
  memcpy(&roll,  data + 17, sizeof(float));

  if (selecting) return;
  float dyaw   = yaw - baselineYaw;
  float dpitch = pitch - baselinePitch;

  // Nod -> LED ON
  if (pitchNeutral && dpitch > NOD_THRESHOLD) {
    digitalWrite(LED_PIN, HIGH);
    selecting    = true;
    pitchNeutral = false;
    Serial.println("[GESTURE] Nod detected -> LED ON");
  } else if (!pitchNeutral && dpitch < NOD_THRESHOLD * 0.5) {
    pitchNeutral = true;
  }

  // Shake -> LED OFF
  if (yawNeutral && fabs(dyaw) > SHAKE_THRESHOLD) {
    digitalWrite(LED_PIN, LOW);
    selecting   = true;
    yawNeutral   = false;
    Serial.println("[GESTURE] Shake detected -> LED OFF");
  } else if (!yawNeutral && fabs(dyaw) < SHAKE_THRESHOLD * 0.5) {
    yawNeutral = true;
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  digitalWrite(LED_PIN, LOW);
  delay(300);
  Serial.println("[SYSTEM] Scanning for SensorTile...");

  NimBLEDevice::init("");
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);

  pClient = NimBLEDevice::createClient();

  NimBLEScan* pScan = NimBLEDevice::getScan();
  pScan->setActiveScan(true);
  pScan->setInterval(45);
  pScan->setWindow(15);

  // Start a 5-second scan
  pScan->start(5, false);  // returns bool, not results
  NimBLEScanResults results = pScan->getResults();
  int count = results.getCount();
  Serial.printf("[BLE] %d devices found\n", count);

  bool connected = false;
  for (int i = 0; i < count; i++) {
    const NimBLEAdvertisedDevice* adv = results.getDevice(i);
    Serial.printf("[BLE] %s  Name:'%s'  RSSI:%d\n",
      adv->getAddress().toString().c_str(),
      adv->getName().c_str(),
      adv->getRSSI()
    );
    if (adv->haveServiceUUID() && adv->isAdvertisingService(serviceUUID)) {
      Serial.println("[BLE] Found SensorTile service, connecting...");
      NimBLEAddress addr = adv->getAddress();
      if (pClient->connect(addr)) {
        Serial.println("[BLE] Connected to SensorTile");
        connected = true;
      } else {
        Serial.println("[BLE] Connection failed");
      }
      break;
    }
  }
  if (!connected) {
    Serial.println("[BLE] Could not connect to SensorTile");
    return;
  }

  NimBLERemoteService* service = pClient->getService(serviceUUID);
  if (!service) {
    Serial.println("[BLE] Service not found");
    return;
  }
  pRemoteChar = service->getCharacteristic(charUUID);
  if (!pRemoteChar) {
    Serial.println("[BLE] Char not found");
    return;
  }
  if (pRemoteChar->canNotify()) {
    pRemoteChar->subscribe(true, notifyCallback);
    Serial.println("[BLE] Subscribed to notifications");
  }
}

void loop() {
  // Connection status every 5s
  static uint32_t prev = 0;
  if (millis() - prev > 5000) {
    prev = millis();
    Serial.print("[BLE] Status: ");
    Serial.println(pClient->isConnected() ? "Connected" : "Disconnected");
  }

  // Calibration
  if (selecting && digitalRead(BUTTON_PIN) == LOW) {
    if (pRemoteChar && pRemoteChar->canRead()) {
      std::string raw = pRemoteChar->readValue();
      if (raw.length() >= 21) {
        memcpy(&baselineYaw,   raw.data() + 9,  sizeof(float));
        memcpy(&baselinePitch, raw.data() + 13, sizeof(float));
        selecting    = false;
        yawNeutral   = true;
        pitchNeutral = true;
        Serial.printf("[CAL] Baseline: Yaw=%.2f, Pitch=%.2f\n", baselineYaw, baselinePitch);
      }
    }
    delay(500);
  }
}

// Upload to ESP32-CAM: button GPIO15->GND, LED GPIO2->GND
