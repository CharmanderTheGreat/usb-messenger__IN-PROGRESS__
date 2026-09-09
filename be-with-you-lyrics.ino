#include <Wire.h>
#include <LiquidCrystal_I2C.h>

LiquidCrystal_I2C lcd(0x27, 16, 2);

struct WordStep {
  unsigned long timeMs;
  const char* word;
  uint8_t col;
  uint8_t row;
  bool clearScreen;
};

const WordStep lyrics[] = {
  {  1500, "I'm",     0, 0, true },
  {  1680, "gonna",   4, 0, false },
  {  2060, "work",   10, 0, false },
  {  2400, "on",      0, 1, false },
  {  2740, "my",      3, 1, false },
  {  3040, "temper",  6, 1, false },

  {  3620, "While",   0, 0, true },
  {  3920, "you",     6, 0, false },
  {  4100, "learn",  10, 0, false },
  {  4420, "to",      0, 1, false },
  {  4540, "speak",   3, 1, false },
  {  4940, "your",    9, 1, false },

  {  5240, "mind",    0, 0, true },
  {  5580, "some",    5, 0, false },
  {  5780, "more",   10, 0, false },

  {  6980, "I'll",    0, 0, true },
  {  7200, "be",      5, 0, false },
  {  7460, "more",    8, 0, false },
  {  7860, "of",     13, 0, false },
  {  8240, "a",       0, 1, false },
  {  8480, "shelter", 2, 1, false },

  {  9860, "and",     0, 0, true },
  { 10180, "less",    4, 0, false },
  { 10580, "of",      9, 0, false },
  { 10960, "a",       0, 1, false },
  { 11160, "storm",   2, 1, false },

  { 12240, "While",   0, 0, true },
  { 12460, "we",      6, 0, false },
  { 12800, "both",    9, 0, false },
  { 13140, "are",     0, 1, false },
  { 13460, "working", 4, 1, false },

  { 14140, "on",        0, 0, true },
  { 14500, "ourselves", 3, 0, false },
  { 15680, "in",        0, 1, false },
  { 15880, "our",       3, 1, false },
  { 16260, "own",       7, 1, false },
  { 16700, "ways",     11, 1, false },

  { 17340, "I",       0, 0, true },
  { 17500, "just",    2, 0, false },
  { 17700, "wanna",   7, 0, false },
  { 18120, "be",     13, 0, false },
  { 18740, "with",    0, 1, false },
  { 18940, "you",     5, 1, false },

  { 19600, "every",   0, 0, true },
  { 20240, "single",  6, 0, false },
  { 20980, "day",     0, 1, false },

  { 22820, "Oh,",     0, 0, true },
  { 23000, "I",       4, 0, false },
  { 23160, "wanna",   6, 0, false },
  { 23540, "be",     12, 0, false },
  { 24160, "with",    0, 1, false },
  { 24400, "you",     5, 1, false },

  { 25060, "every",   0, 0, true },
  { 25700, "single",  6, 0, false },
  { 26260, "day",     0, 1, false },

  { 30280, "For",     0, 0, true },
  { 30480, "the",     4, 0, false },
  { 30680, "rest",    8, 0, false },
  { 31040, "of",      0, 1, false },
  { 31200, "our",     3, 1, false },
  { 31680, "lives",   7, 1, false }
};

const int totalWords = sizeof(lyrics) / sizeof(lyrics[0]);
int currentWordIndex = 0;
unsigned long startPlaybackTime = 0;

void setup() {
  lcd.init();
  lcd.backlight();
  lcd.clear();

  lcd.setCursor(0, 0);
  lcd.print("@rence.szkyrob");
  delay(1300);
  lcd.clear();

  startPlaybackTime = millis();
}

void loop() {
  unsigned long elapsed = millis() - startPlaybackTime;

  if (currentWordIndex < totalWords) {
    if (elapsed >= lyrics[currentWordIndex].timeMs) {
      if (lyrics[currentWordIndex].clearScreen) {
        lcd.clear();
      }

      lcd.setCursor(lyrics[currentWordIndex].col, lyrics[currentWordIndex].row);
      lcd.print(lyrics[currentWordIndex].word);

      currentWordIndex++;
    }
  }
}