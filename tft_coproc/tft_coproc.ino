// tft_coproc.ino
// ATmega328P (5V / 16MHz, UNO互換配線) + 3.95" ILI9488 8bit UNOシールド
// Pi Zero から UART(250000bps) の行コマンドで描画し、タッチ座標を返す表示コプロセッサ。
//
// ---- プロトコル (ASCII, 1行1コマンド, '\n'終端) ----
// Pi -> 328P                         328P -> Pi
//   C color                            K           各コマンド実行完了(ACK)
//   R x y w h color   塗り矩形         D x y       タッチ押下(画面座標)
//   B x y w h color   枠矩形           U           タッチ解放
//   L x0 y0 x1 y1 color 直線          READY id    起動完了(idはLCDコントローラ)
//   T x y size fg bg text  文字(bg=-1で透過, ASCIIのみ)
//   P                 疎通確認
//   M 0|1             1: 押下時 "A rx ry"(ADC生値)を送る。0: "D x y"(画面座標)
// 色は RGB565 (10進 or 0x表記)。Piは K を受けてから次の行を送る(フロー制御)。
//
// 必要ライブラリ: MCUFRIEND_kbv, Adafruit GFX Library (タッチは自前で読むので TouchScreen は不要)

#include <MCUFRIEND_kbv.h>
// (タッチは自前で読む: readTouch)

MCUFRIEND_kbv tft;

// ===== ここを TouchScreen_Calibr_native の "COPY-PASTE" 出力2行で置き換える =====
const int XP = 6, XM = A2, YP = A1, YM = 7;   // Calibr_native 2026-09-25 のピン検出結果
const int TS_LEFT = 691, TS_RT = 155, TS_TOP = 919, TS_BOT = 169;  // PORTRAIT 320x480
// ==============================================================================


// Adafruit TouchScreen の z は接触抵抗(強く押すほど小さい、軽いと1000超もある)。
// Calibr_native と同じく「z > 200 なら押下」で判定し上限は設けない。
const int MINPRESSURE = 200;
static bool rawDebug = false;
static bool rawMode = false;    // 'M 1' で押下時に生値 "A rx ry" を送る(校正はPi側)   // 'Z' コマンドで生値出力を切替

static char line[100];
static uint8_t len = 0;
static char *pp;

static bool touching = false;
static uint8_t downCount = 0;
static uint8_t upCount = 0;
static unsigned long lastPoll = 0;

static long num() { return strtol(pp, &pp, 0); }

static void execLine() {
  line[len] = 0;
  pp = line + 1;
  switch (line[0]) {
    case 'C':
      tft.fillScreen((uint16_t)num());
      break;
    case 'R': {
      int x = num(), y = num(), w = num(), h = num();
      tft.fillRect(x, y, w, h, (uint16_t)num());
    } break;
    case 'B': {
      int x = num(), y = num(), w = num(), h = num();
      tft.drawRect(x, y, w, h, (uint16_t)num());
    } break;
    case 'L': {
      int x0 = num(), y0 = num(), x1 = num(), y1 = num();
      tft.drawLine(x0, y0, x1, y1, (uint16_t)num());
    } break;
    case 'T': {
      int x = num(), y = num(), s = num();
      long fg = num(), bg = num();
      if (*pp == ' ') pp++;               // 区切りの空白1個だけ読み飛ばす
      tft.setCursor(x, y);
      tft.setTextSize(s < 1 ? 1 : s);
      if (bg < 0) tft.setTextColor((uint16_t)fg);
      else        tft.setTextColor((uint16_t)fg, (uint16_t)bg);
      tft.print(pp);
    } break;
    case 'M':  // M 1: 生値モード / M 0: 従来の D x y
      rawMode = num() != 0;
      break;
    case 'Z':  // デバッグ: タッチ生値 "Z x y z" 出力の ON/OFF
      rawDebug = !rawDebug;
      break;
    default:  // 'P' と未知コマンドは何もしない
      break;
  }
  Serial.print('K');
  Serial.print('\n');
}

// ---- タッチ読み取り (Adafruit TouchScreen と同じ計算式・同じ向き。生値は互換) ----
// ライブラリは端子を切り替えた直後にAD変換するため、膜の抵抗が高い場所(このパネルでは左側)や
// 描画直後は電圧が落ち着く前の値を読んでしまう。切替後に待ち時間を入れ、2回平均する。
static const uint16_t SETTLE_US = 150;
static const int MAXPRESSURE = 20000;   // 負値/異常な巨大値(膜の短絡時など)は無効
static const uint8_t STABLE_N = 3;      // 連続して安定した回数で押下を確定
static const int STABLE_TOL = 12;       // 安定とみなす生値のばらつき

static int readA(uint8_t pin) {
  (void)analogRead(pin);                // 1回目は捨てる
  return (analogRead(pin) + analogRead(pin)) / 2;
}

static void readTouch(int &x, int &y, int &z) {
  // X: XP=H, XM=L を印加し YP で読む
  pinMode(YP, INPUT); pinMode(YM, INPUT);
  digitalWrite(YP, LOW); digitalWrite(YM, LOW);
  pinMode(XP, OUTPUT); pinMode(XM, OUTPUT);
  digitalWrite(XP, HIGH); digitalWrite(XM, LOW);
  delayMicroseconds(SETTLE_US);
  x = 1023 - readA(YP);
  // Y: YP=H, YM=L を印加し XM で読む
  pinMode(XP, INPUT); pinMode(XM, INPUT);
  digitalWrite(XP, LOW); digitalWrite(XM, LOW);
  pinMode(YP, OUTPUT); pinMode(YM, OUTPUT);
  digitalWrite(YM, LOW); digitalWrite(YP, HIGH);
  delayMicroseconds(SETTLE_US);
  y = 1023 - readA(XM);
  // Z(接触抵抗): XP=L, YM=H, XM と YP で読む
  pinMode(XP, OUTPUT); pinMode(YP, INPUT);
  digitalWrite(XP, LOW); digitalWrite(YM, HIGH);
  delayMicroseconds(SETTLE_US);
  int z1 = readA(XM);
  int z2 = readA(YP);
  if (z1 <= 0) {
    z = 0;
  } else {
    float r = ((float)z2 / z1 - 1.0f) * x * 300.0f / 1024.0f;  // rxplate=300
    z = (r > 32000.0f) ? 32000 : (r < -32000.0f) ? -32000 : (int)r;
  }
  // LCD 制御線(WR/RS)とデータ線を描画できる状態に戻す
  pinMode(YP, OUTPUT); pinMode(XM, OUTPUT);
  digitalWrite(YP, HIGH); digitalWrite(XM, HIGH);
}

static int histX[STABLE_N], histY[STABLE_N];

static void pollTouch() {
  int px, py, pz;
  readTouch(px, py, pz);

  if (rawDebug && pz != 0) {
    Serial.print(F("Z "));
    Serial.print(px); Serial.print(' ');
    Serial.print(py); Serial.print(' ');
    Serial.print(pz); Serial.print('\n');
  }
  bool down = (pz > MINPRESSURE && pz < MAXPRESSURE);
  if (down) {
    upCount = 0;
    if (touching) return;
    histX[downCount % STABLE_N] = px;
    histY[downCount % STABLE_N] = py;
    downCount++;
    if (downCount < STABLE_N + 1) return;   // 押し始めの1回は捨て、その後 STABLE_N 回集める
    int mnx = 1023, mxx = 0, mny = 1023, mxy = 0;
    long sx = 0, sy = 0;
    for (uint8_t i = 0; i < STABLE_N; i++) {
      mnx = min(mnx, histX[i]); mxx = max(mxx, histX[i]);
      mny = min(mny, histY[i]); mxy = max(mxy, histY[i]);
      sx += histX[i]; sy += histY[i];
    }
    if (mxx - mnx > STABLE_TOL || mxy - mny > STABLE_TOL) {
      if (downCount > 40) downCount = 1;    // 揺れ続けるときは数え直す
      return;                               // まだ落ち着いていない
    }
    touching = true;
    int ax = sx / STABLE_N, ay = sy / STABLE_N;
    if (rawMode) {
      Serial.print(F("A "));
      Serial.print(ax);
      Serial.print(' ');
      Serial.print(ay);
      Serial.print('\n');
      return;
    }
    int x = map(ax, TS_LEFT, TS_RT, 0, tft.width());
    int y = map(ay, TS_TOP, TS_BOT, 0, tft.height());
    x = constrain(x, 0, tft.width() - 1);
    y = constrain(y, 0, tft.height() - 1);
    Serial.print(F("D "));
    Serial.print(x);
    Serial.print(' ');
    Serial.print(y);
    Serial.print('\n');
  } else {
    downCount = 0;
    if (touching && ++upCount >= 3) {
      touching = false;
      Serial.print(F("U\n"));
    }
  }
}

void setup() {
  Serial.begin(250000);   // 16MHzで誤差0%
  // 電源投入直後は ID 読み出しが化けることがある(例: 0xA0A0)。数回リトライし、
  // それでも ILI9488 と読めなければ 0x9488 で強制初期化する(型番は確定済み)。
  uint16_t readId = 0, id = 0x9488;
  delay(100);
  for (uint8_t i = 0; i < 5; i++) {
    tft.reset();
    delay(20);
    readId = tft.readID();
    if (readId == 0x9488) break;
  }
  tft.begin(id);
  tft.setRotation(0);     // 縦 320x480
  tft.fillScreen(0x0000);
  tft.setTextSize(2);
  tft.setTextColor(0xFFFF);
  tft.setCursor(10, 10);
  tft.print(F("Waiting for Pi..."));
  Serial.print(F("READY "));
  Serial.print(id, HEX);
  Serial.print(F(" read="));
  Serial.println(readId, HEX);
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      if (len) execLine();
      len = 0;
    } else if (c != '\r' && len < sizeof(line) - 1) {
      line[len++] = c;
    }
  }
  // 受信途中の行が無いときだけタッチを読む(読み取り中は約1ms受信を見ないため)
  if (len == 0 && millis() - lastPoll >= 15) {
    lastPoll = millis();
    pollTouch();
  }
}
