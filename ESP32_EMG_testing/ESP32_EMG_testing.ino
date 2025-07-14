#include <WiFi.h>
#include <PubSubClient.h>

// Wi-Fi
const char* ssid = "xxx";
const char* password = "xxx";

// MQTT
const char* mqtt_server = "broker.hivemq.com";
const char* mqtt_topic = "emg/control";

// ESP32-CAM built-in LED pin
#define BUILTIN_FLASH 4

WiFiClient espClient;
PubSubClient client(espClient);

void setup_wifi() {
  Serial.begin(115200);
  delay(10);

  Serial.println();
  Serial.print("Connecting to ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi connected");
  Serial.println("IP address: ");
  Serial.println(WiFi.localIP());
}

void callback(char* topic, byte* payload, unsigned int length) {
  String message;
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }

  Serial.print("MQTT received: ");
  Serial.println(message);

  if (message == "light_on") {
    digitalWrite(BUILTIN_FLASH, HIGH);
    Serial.println("Built-in light ON");
  } else if (message == "light_off") {
    digitalWrite(BUILTIN_FLASH, LOW);
    Serial.println("Built-in light OFF");
  } else if (message == "spike_detected") {
    Serial.println("Detected spike - you can add logic here!");
  }
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("Connecting to MQTT...");
    if (client.connect("ESP32CAMClient")) {
      Serial.println("connected");
      client.subscribe(mqtt_topic);
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" retrying...");
      delay(2000);
    }
  }
}

void setup() {
  pinMode(BUILTIN_FLASH, OUTPUT);
  digitalWrite(BUILTIN_FLASH, LOW);

  setup_wifi();
  client.setServer(mqtt_server, 1883);
  client.setCallback(callback);
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();
}
