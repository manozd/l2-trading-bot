# Pico serial HID (market bot)

Firmware: `hardware/pico_serial_hid/code.py`

## Flash once

Market search needs extra keys beyond basic `CLICK` and `a-z`:

- `KEY SPACE`, `KEY MINUS`, `KEY LPAREN`, `KEY RPAREN`, `KEY PERCENT`, `KEY COLON`, `KEY ENTER`

1. Plug in Pico → open the `CIRCUITPY` drive
2. Replace `code.py` with `hardware/pico_serial_hid/code.py` from this repo
3. Pico reboots automatically

## Test typing

```powershell
python -m cli test-keys --pico-com COM3 --delay 8 --text "Angel Slayer"
```

Focus the in-game search box during the countdown.

## Calibrate + run

```powershell
python -m cli calibrate market
python -m cli calibrate search
python -m cli calibrate back

python -m cli run
# M+1 bulk, M+2 priority monitor, F12 start/stop
```

PC moves the cursor; the Pico sends **CLICK** and **KEY** (USB HID, GameGuard-safe).
