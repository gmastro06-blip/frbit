/*
 * tibia_hid.ino — Arduino Leonardo/Pro Micro HID Emulator
 * =========================================================
 *
 * Receives serial commands from the Python ArduinoHIDController
 * and emulates real USB HID keyboard/mouse events that are
 * indistinguishable from physical hardware for BattlEye.
 *
 * Protocol (newline-terminated, 115200 baud):
 *   PING                          → PONG
 *   KEY_PRESS|<key>|<duration_ms> → ACK  (press, hold, release)
 *   KEY_RELEASE|<key>             → ACK
 *   MOUSE_MOVE|<x>|<y>|<rel>     → ACK  (rel: 0=absolute, 1=relative)
 *   MOUSE_CLICK|<button>          → ACK  (LEFT / RIGHT / MIDDLE)
 *
 * Board: Arduino Leonardo, Pro Micro, or any ATmega32u4 board.
 * Libraries: Keyboard.h, Mouse.h (built-in with Leonardo core).
 *
 * Upload:
 *   Arduino IDE → Board: "Arduino Leonardo" → Upload
 */

#include <Keyboard.h>
#include <Mouse.h>

static const unsigned long BAUD_RATE = 115200;
static const int MAX_CMD_LEN = 128;

static char cmdBuf[MAX_CMD_LEN];
static int  cmdIdx = 0;

/* -------------------------------------------------- */
/*  Helpers                                          */
/* -------------------------------------------------- */

/* Parse a single key string to a Keyboard key code. */
static uint8_t parseKey(const char* keyStr) {
    /* Function keys F1-F12 */
    if (keyStr[0] == 'F' || keyStr[0] == 'f') {
        int n = atoi(keyStr + 1);
        if (n >= 1 && n <= 12) {
            return KEY_F1 + (n - 1);
        }
    }

    /* Special keys */
    if (strcmp(keyStr, "ENTER")  == 0 || strcmp(keyStr, "RETURN") == 0) return KEY_RETURN;
    if (strcmp(keyStr, "ESC")    == 0 || strcmp(keyStr, "ESCAPE") == 0) return KEY_ESC;
    if (strcmp(keyStr, "TAB")    == 0) return KEY_TAB;
    if (strcmp(keyStr, "SPACE")  == 0) return ' ';
    if (strcmp(keyStr, "BACKSPACE") == 0) return KEY_BACKSPACE;
    if (strcmp(keyStr, "DELETE") == 0) return KEY_DELETE;
    if (strcmp(keyStr, "INSERT") == 0) return KEY_INSERT;
    if (strcmp(keyStr, "HOME")   == 0) return KEY_HOME;
    if (strcmp(keyStr, "END")    == 0) return KEY_END;
    if (strcmp(keyStr, "PAGEUP") == 0) return KEY_PAGE_UP;
    if (strcmp(keyStr, "PAGEDOWN") == 0) return KEY_PAGE_DOWN;
    if (strcmp(keyStr, "UP")     == 0) return KEY_UP_ARROW;
    if (strcmp(keyStr, "DOWN")   == 0) return KEY_DOWN_ARROW;
    if (strcmp(keyStr, "LEFT")   == 0) return KEY_LEFT_ARROW;
    if (strcmp(keyStr, "RIGHT")  == 0) return KEY_RIGHT_ARROW;
    if (strcmp(keyStr, "LCTRL")  == 0 || strcmp(keyStr, "CTRL") == 0) return KEY_LEFT_CTRL;
    if (strcmp(keyStr, "LSHIFT") == 0 || strcmp(keyStr, "SHIFT") == 0) return KEY_LEFT_SHIFT;
    if (strcmp(keyStr, "LALT")   == 0 || strcmp(keyStr, "ALT") == 0)   return KEY_LEFT_ALT;

    /* Single ASCII char */
    if (strlen(keyStr) == 1) {
        return (uint8_t)keyStr[0];
    }

    return 0; /* Unknown key */
}

/* Safe delay that won't exceed sane limits */
static void safeDelay(unsigned long ms) {
    if (ms > 5000) ms = 5000;  /* cap at 5 seconds */
    if (ms > 0) delay(ms);
}

/* -------------------------------------------------- */
/*  Command handlers                                 */
/* -------------------------------------------------- */

static void handlePing() {
    Serial.println("PONG");
}

static void handleKeyPress(char* args) {
    /* args = "<key>|<duration_ms>" */
    char* key = strtok(args, "|");
    char* durStr = strtok(NULL, "|");
    if (!key) { Serial.println("ERR"); return; }

    uint8_t k = parseKey(key);
    if (k == 0) { Serial.println("ERR"); return; }

    unsigned long dur = durStr ? strtoul(durStr, NULL, 10) : 80;

    Keyboard.press(k);
    safeDelay(dur);
    Keyboard.release(k);

    Serial.println("ACK");
}

static void handleKeyRelease(char* args) {
    /* args = "<key>" */
    char* key = strtok(args, "|");
    if (!key) { Serial.println("ERR"); return; }

    uint8_t k = parseKey(key);
    if (k == 0) { Serial.println("ERR"); return; }

    Keyboard.release(k);
    Serial.println("ACK");
}

static void handleMouseMove(char* args) {
    /* args = "<x>|<y>|<rel>" */
    char* xStr   = strtok(args, "|");
    char* yStr   = strtok(NULL, "|");
    char* relStr = strtok(NULL, "|");
    if (!xStr || !yStr) { Serial.println("ERR"); return; }

    int x = atoi(xStr);
    int y = atoi(yStr);
    bool rel = relStr ? (atoi(relStr) != 0) : true;

    if (rel) {
        /* Relative move — chunk large moves into <=127 steps */
        while (x != 0 || y != 0) {
            int dx = constrain(x, -127, 127);
            int dy = constrain(y, -127, 127);
            Mouse.move(dx, dy, 0);
            x -= dx;
            y -= dy;
        }
    } else {
        /* Absolute: not directly supported by Mouse.h.
         * Move to (0,0) first, then relative to target.
         * NOTE: This is approximate. For absolute positioning,
         * prefer relative mode from Python side. */
        Mouse.move(-16383, -16383, 0); /* force to origin */
        delay(5);
        /* Now move to target in steps */
        int rx = x, ry = y;
        while (rx != 0 || ry != 0) {
            int dx = constrain(rx, -127, 127);
            int dy = constrain(ry, -127, 127);
            Mouse.move(dx, dy, 0);
            rx -= dx;
            ry -= dy;
        }
    }

    Serial.println("ACK");
}

static void handleMouseClick(char* args) {
    /* args = "<button>" */
    char* btn = strtok(args, "|");
    if (!btn) { Serial.println("ERR"); return; }

    uint8_t button = MOUSE_LEFT;
    if (strcmp(btn, "RIGHT") == 0)  button = MOUSE_RIGHT;
    if (strcmp(btn, "MIDDLE") == 0) button = MOUSE_MIDDLE;

    Mouse.press(button);
    delay(50 + random(10, 60));  /* Human-like click duration */
    Mouse.release(button);

    Serial.println("ACK");
}

/* -------------------------------------------------- */
/*  Command dispatcher                               */
/* -------------------------------------------------- */

static void processCommand(char* cmd) {
    /* Strip trailing whitespace/CR/LF */
    int len = strlen(cmd);
    while (len > 0 && (cmd[len-1] == '\r' || cmd[len-1] == '\n' || cmd[len-1] == ' ')) {
        cmd[--len] = '\0';
    }

    if (len == 0) return;

    /* Extract command name */
    char* verb = strtok(cmd, "|");
    char* rest = cmd + strlen(verb) + 1;  /* Points past first '|' */
    if (strlen(verb) == (size_t)len) rest = NULL;

    if (strcmp(verb, "PING") == 0)         { handlePing(); return; }
    if (strcmp(verb, "KEY_PRESS") == 0)     { handleKeyPress(rest); return; }
    if (strcmp(verb, "KEY_RELEASE") == 0)   { handleKeyRelease(rest); return; }
    if (strcmp(verb, "MOUSE_MOVE") == 0)    { handleMouseMove(rest); return; }
    if (strcmp(verb, "MOUSE_CLICK") == 0)   { handleMouseClick(rest); return; }

    Serial.println("ERR");  /* Unknown command */
}

/* -------------------------------------------------- */
/*  Arduino lifecycle                                */
/* -------------------------------------------------- */

void setup() {
    Serial.begin(BAUD_RATE);
    while (!Serial) { ; }  /* Wait for USB enumeration */

    Keyboard.begin();
    Mouse.begin();

    randomSeed(analogRead(0));  /* Seed RNG for human-like jitter */

    cmdIdx = 0;
}

void loop() {
    while (Serial.available() > 0) {
        char c = (char)Serial.read();
        if (c == '\n') {
            cmdBuf[cmdIdx] = '\0';
            processCommand(cmdBuf);
            cmdIdx = 0;
        } else if (cmdIdx < MAX_CMD_LEN - 1) {
            cmdBuf[cmdIdx++] = c;
        }
        /* Overflow: silently discard until next newline */
    }
}
