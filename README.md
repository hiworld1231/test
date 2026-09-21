# Linux sound detector (PipeWire/Pulse)

Детектор под CachyOS/Arch: слушает реальный аудиовыход через PipeWire/Pulse monitor и ищет заранее известный звук внутри общего микса.

Он не читает память Roblox/Sober, не внедряется в процесс и не зависит от Roblox cache.

Для присланного звука уже лежит готовый `target_fingerprint.json`. Сам OGG для обычного запуска не нужен.

## Установка

```bash
sudo pacman -S --needed python python-numpy python-scipy ffmpeg libpulse
```

## Запуск

```bash
python detector.py
```

После запуска программа молчит. В консоль выводятся только реальные срабатывания, например:

```text
[22:15:01] DETECTED #1 (votes=82, runner=11)
[22:23:44] DETECTED #2 (votes=76, runner=9)
```

Остановить:

```text
Ctrl+C
```

После остановки будет итог:

```text
Detected total: 2
```

Один и тот же проигрываемый звук не должен спамить несколькими событиями подряд: после срабатывания детектор блокируется и ждёт, пока target исчезнет из аудиопотока, прежде чем снова разрешить счёт.

## Выполнить действие при детекте

Например, уведомление KDE:

```bash
python detector.py --command 'notify-send "Target sound" "detected"'
```

Или свой скрипт:

```bash
python detector.py --command './action.sh'
```

## Обновить уже клонированный репозиторий

```bash
git pull
python detector.py
```

## Если выбран не тот аудиовыход

Посмотреть monitor sources:

```bash
pactl list short sources
```

И указать нужный вручную:

```bash
python detector.py --source 'alsa_output....monitor'
```

По умолчанию берётся monitor текущего default sink.

## Настройка чувствительности

Обычно ничего менять не нужно.

Если target иногда пропускается:

```bash
python detector.py --threshold 35
```

Если появляются ложные срабатывания:

```bash
python detector.py --threshold 60
```

Дополнительно:

- `--threshold 45` — минимальное количество fingerprint votes.
- `--ratio 1.6` — насколько лучший временной offset должен превосходить второй.
- `--window 0.70` — окно анализа.
- `--step 0.10` — частота проверки.
- `--cooldown 1.0` — абсолютный минимум между событиями.
- `--rearm 0.45` — сколько target должен отсутствовать перед разрешением следующего события.

## Другой target

```bash
python build_fingerprint.py other.ogg -o other.json
python detector.py --fingerprint other.json
```
