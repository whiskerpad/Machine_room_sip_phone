// touch_diag.ino - 4線式 抵抗膜タッチパネルの故障診断（Arduino UNO / ATmega328P 単体で動かす）
//
// 3.95" ILI9488 UNOシールド用。Pi は使わず、UNO を PC の USB につないでシリアルモニタ(115200bps)で見る。
// 液晶ライブラリは不要（液晶は CS=HIGH で休ませ、タッチの4本だけを測る）。
//
//   1) 膜の導通     : X膜(XP-XM) と Y膜(YP-YM) が切れていないか、抵抗はおおよそいくらか
//   2) 無操作の絶縁 : 触っていないのに X膜と Y膜がつながっていないか
//   3) 押下テスト   : 押したときに膜同士が接触するか（校正値や座標計算に関係なく判定）
//                     と、そのときの生値 X / Y / Z（Adafruit TouchScreen と同じ計算）
//
// シリアルモニタで送るコマンド（改行は何でもよい）:
//   r : 1) 2) を測り直す        s : 押下テストの集計を表示してリセット
//
// ピンは tft_coproc.ino と同じ（TouchScreen_Calibr_native の検出結果）。違うシールドなら書き換える。
const uint8_t XP = 6, YM = 7;        // デジタルピン
const uint8_t XM = A2, YP = A1;      // アナログピン
const uint8_t LCD_RD = A0, LCD_CS = A3, LCD_RST = A4;

const float RPU = 35000.0;           // 328P 内蔵プルアップの目安(20〜50kΩ)。抵抗値は概算になる
const int TOUCH_DET = 600;           // 押下検出: 膜接触で YP の電圧がこれより下がったら「接触」

// ---- 集計（押下テスト） ----
static unsigned long nPoll = 0, nDet = 0, nZok = 0, nRail = 0;
static int xmin = 1023, xmax = 0, ymin = 1023, ymax = 0;
static bool lastDet = false;
static unsigned long lastPrint = 0;

void lcdIdle() {                     // 液晶を非選択にして、共用ピンを触っても描画が乱れないようにする
  pinMode(LCD_CS, OUTPUT);  digitalWrite(LCD_CS, HIGH);
  pinMode(LCD_RD, OUTPUT);  digitalWrite(LCD_RD, HIGH);
  pinMode(LCD_RST, OUTPUT); digitalWrite(LCD_RST, HIGH);
}

void releaseAll() {                  // 4本とも入力(プルアップなし)に戻す
  pinMode(XP, INPUT); pinMode(XM, INPUT); pinMode(YP, INPUT); pinMode(YM, INPUT);
  digitalWrite(XP, LOW); digitalWrite(XM, LOW); digitalWrite(YP, LOW); digitalWrite(YM, LOW);
}

int readA(uint8_t pin) {             // 切り替え直後は電圧が落ち着かないので待ってから平均
  delayMicroseconds(300);
  (void)analogRead(pin);
  long s = 0;
  for (uint8_t i = 0; i < 8; i++) s += analogRead(pin);
  return (int)(s / 8);
}

float toOhm(int v) {                 // プルアップと膜の分圧から膜の抵抗を概算
  if (v >= 1020) return -1;          // 開放(断線)
  return RPU * v / (1023.0 - v);
}

// 片側を LOW、もう片側をプルアップして電圧を読む（膜が通っていれば 0 付近、切れていれば 1023）
int plateX() { releaseAll(); pinMode(XP, OUTPUT); digitalWrite(XP, LOW); pinMode(XM, INPUT_PULLUP); return readA(XM); }
int plateY() { releaseAll(); pinMode(YM, OUTPUT); digitalWrite(YM, LOW); pinMode(YP, INPUT_PULLUP); return readA(YP); }

// X膜の両端を LOW、Y膜をプルアップ: 触っていなければ 1023 付近、接触すると下がる
int contact() {
  releaseAll();
  pinMode(XP, OUTPUT); pinMode(XM, OUTPUT); digitalWrite(XP, LOW); digitalWrite(XM, LOW);
  pinMode(YP, INPUT_PULLUP);
  return readA(YP);
}

void readXYZ(int &x, int &y, int &z) {   // Adafruit TouchScreen と同じ式（tft_coproc の readTouch と同じ値）
  releaseAll();
  pinMode(XP, OUTPUT); pinMode(XM, OUTPUT); digitalWrite(XP, HIGH); digitalWrite(XM, LOW);
  x = 1023 - readA(YP);
  releaseAll();
  pinMode(YP, OUTPUT); pinMode(YM, OUTPUT); digitalWrite(YP, HIGH); digitalWrite(YM, LOW);
  y = 1023 - readA(XM);
  releaseAll();
  pinMode(XP, OUTPUT); digitalWrite(XP, LOW); pinMode(YM, OUTPUT); digitalWrite(YM, HIGH);
  int z1 = readA(XM), z2 = readA(YP);
  if (z1 <= 0) { z = 0; return; }
  float r = ((float)z2 / z1 - 1.0f) * x * 300.0f / 1024.0f;
  z = (r > 32000.0f) ? 32000 : (r < -32000.0f) ? -32000 : (int)r;
}

void printPlate(const __FlashStringHelper *name, int v, bool &ok) {
  float r = toOhm(v);
  Serial.print(name); Serial.print(F(" ADC=")); Serial.print(v);
  if (r < 0) {
    Serial.println(F("  -> NG 断線（フィルムの配線・FPCの割れ、ピンのはんだ不良）"));
    ok = false;
  } else {
    Serial.print(F("  約")); Serial.print((long)r); Serial.print(F("Ω"));
    if (r > 3000) { Serial.println(F("  -> WARN 抵抗が高い（接触不良・電極の劣化の疑い）")); ok = false; }
    else          Serial.println(F("  -> OK"));
  }
}

void staticTests() {
  Serial.println();
  Serial.println(F("==== 1) 膜の導通 ===="));
  bool ok = true;
  printPlate(F("X膜 (XP=D6 - XM=A2)"), plateX(), ok);
  printPlate(F("Y膜 (YP=A1 - YM=D7)"), plateY(), ok);
  Serial.println(F("  正常の目安: 各 200〜1000Ω 程度（概算。テスターで実測するとより確実）"));

  Serial.println(F("==== 2) 無操作の絶縁（3秒間、画面に触らないで） ===="));
  for (int i = 3; i > 0; i--) { Serial.print(i); Serial.print(F("..")); delay(1000); }
  int vmin = 1023;
  for (int i = 0; i < 50; i++) { int v = contact(); if (v < vmin) vmin = v; delay(10); }
  Serial.print(F("\n  YP 最低値=")); Serial.print(vmin);
  if (vmin < TOUCH_DET) {
    Serial.println(F("  -> NG 触っていないのに X膜とY膜が接触（膜の貼り付き・圧迫・はんだブリッジ）"));
    ok = false;
  } else if (vmin < 950) {
    Serial.println(F("  -> WARN 少し漏れがある（湿気・汚れ・軽い圧迫）"));
  } else {
    Serial.println(F("  -> OK"));
  }
  Serial.println(ok ? F("  静的テスト: 異常なし") : F("  静的テスト: 異常あり（上の NG / WARN を参照）"));

  Serial.println(F("==== 3) 押下テスト ===="));
  Serial.println(F("  画面を指やペンで押してください。四隅・四辺・中央を順に、端から端までなぞるのも有効。"));
  Serial.println(F("  押している間、det(接触の強さ, 小さいほど強い) と 生値 x y z を表示します。"));
  Serial.println(F("  終わったら 's' を送ると集計します。"));
  nPoll = nDet = nZok = nRail = 0; xmin = ymin = 1023; xmax = ymax = 0;
}

void summary() {
  Serial.println();
  Serial.println(F("==== 押下テストの集計 ===="));
  Serial.print(F("  接触を検出した回数 det: ")); Serial.println(nDet);
  if (nDet == 0) {
    Serial.println(F("  -> NG 押しても膜同士が接触しない。膜の浮き・スペーサーの破損、"));
    Serial.println(F("        または 1) で断線が出ているならその影響。強めにペンで押しても同じなら故障。"));
    return;
  }
  Serial.print(F("  z が有効(200〜20000)だった割合: ")); Serial.print(100UL * nZok / nDet); Serial.println(F(" %"));
  Serial.print(F("  生値 X の範囲: ")); Serial.print(xmin); Serial.print('-'); Serial.print(xmax);
  Serial.print(F("  (幅 ")); Serial.print(xmax - xmin); Serial.println(F(", 正常なら端から端で 400 以上)"));
  Serial.print(F("  生値 Y の範囲: ")); Serial.print(ymin); Serial.print('-'); Serial.print(ymax);
  Serial.print(F("  (幅 ")); Serial.print(ymax - ymin); Serial.println(F(", 正常なら 550 以上)"));
  Serial.print(F("  生値が 0 か 1023 に張り付いた回数: ")); Serial.println(nRail);
  bool bad = false;
  if (100UL * nZok / nDet < 50) {
    Serial.println(F("  -> WARN 接触はしているが z が無効になりがち（押下判定されにくい）。軽いタッチでは反応しない状態"));
    bad = true;
  }
  if (nRail * 4 > nDet) {
    Serial.println(F("  -> NG 生値が端に張り付く。片側の電極（膜の端の銀線）が切れている疑い"));
    bad = true;
  }
  if (xmax - xmin < 250 || ymax - ymin < 350) {
    Serial.println(F("  -> WARN 生値の幅が狭い（画面全体をなぞっていないなら無視してよい）"));
    bad = true;
  }
  if (!bad) {
    Serial.println(F("  -> パネルは正常に反応しています。"));
    Serial.println(F("     反応しない原因は パネル以外（Pi 側の校正ファイル・配線・328P のプログラム）を疑ってください。"));
  }
  nPoll = nDet = nZok = nRail = 0; xmin = ymin = 1023; xmax = ymax = 0;
}

void setup() {
  Serial.begin(115200);
  lcdIdle();
  releaseAll();
  delay(300);
  Serial.println(F("\n#### touch_diag : 抵抗膜タッチパネル診断 ####"));
  Serial.println(F("ピン: XP=D6 XM=A2 YP=A1 YM=D7  (違う場合はスケッチ先頭を変更)"));
  staticTests();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'r' || c == 'R') staticTests();
    else if (c == 's' || c == 'S') summary();
  }
  int det = contact();
  nPoll++;
  bool on = det < TOUCH_DET;
  if (on) {
    int x, y, z;
    readXYZ(x, y, z);
    nDet++;
    if (z > 200 && z < 20000) nZok++;
    if (x <= 5 || x >= 1018 || y <= 5 || y >= 1018) nRail++;
    else {
      if (x < xmin) xmin = x;
      if (x > xmax) xmax = x;
      if (y < ymin) ymin = y;
      if (y > ymax) ymax = y;
    }
    if (millis() - lastPrint > 120) {
      lastPrint = millis();
      Serial.print(F("det=")); Serial.print(det);
      Serial.print(F(" x=")); Serial.print(x);
      Serial.print(F(" y=")); Serial.print(y);
      Serial.print(F(" z=")); Serial.print(z);
      Serial.println((z > 200 && z < 20000) ? F("  (押下と判定される)") : F("  (z が範囲外: tft_coproc では押下にならない)"));
    }
  } else if (lastDet) {
    Serial.println(F("-- 離した --"));
  }
  lastDet = on;
  delay(15);
}
