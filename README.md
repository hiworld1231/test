# Linux sound detector (PipeWire/Pulse)

Прототип под CachyOS/Arch: слушает **реальный аудиовыход** через monitor-source PipeWire/PulseAudio и ищет заранее известный звук внутри общего микса.

Он не читает память Roblox/Sober, не внедряется в процесс и не зависит от Roblox cache. Детектор работает с PCM-потоком, который уже отправляется на колонки/наушники.

Для присланного звука используется готовый `target_fingerprint.json`; сам OGG для обычного запуска не нужен.

## Установка на CachyOS / Arch

```bash
sudo pacman -S --needed python python-numpy python-scipy ffmpeg libpulse
```

Проверь monitor-source:

```bash
pactl get-default-sink
pactl list short sources
```

Обычно нужный source заканчивается на `.monitor`.

## Запуск

```bash
python detector.py
```

При совпадении:

```text
[22:15:01] TARGET DETECTED #1 votes=123 runner_up=20 matched=170
```

Чтобы выполнить действие:

```bash
python detector.py --command 'notify-send "Target sound" "detected"'
```

Или любой свой скрипт:

```bash
python detector.py --command './action.sh'
```

## Если auto выбрал не тот monitor

```bash
pactl list short sources
python detector.py --source 'alsa_output....monitor'
```

По умолчанию слушается monitor **default sink**, то есть весь звук на текущем устройстве вывода. Другие программы и игровые эффекты могут звучать одновременно: fingerprint ищет согласованную во времени комбинацию спектральных пиков, а не полное совпадение waveform.

## Настройка

Для начала:

```bash
python detector.py --debug
```

Основные параметры:

- `--threshold 45` — минимальное число fingerprint-votes. Ниже = чувствительнее, но выше риск false positive.
- `--ratio 1.6` — лучший временной offset должен выигрывать у второго места.
- `--window 0.70` — rolling window в секундах.
- `--step 0.10` — период проверки.
- `--cooldown 1.5` — защита от повторного trigger одного проигрывания.

На чистом присланном файле стандартный порог пересекается примерно через **0.4 с** от начала. В реальной игре задержка зависит от громкости target и количества одновременно звучащих эффектов.

## Сделать fingerprint для другого звука

```bash
python build_fingerprint.py other.ogg -o other.json
python detector.py --fingerprint other.json
```

## Как это работает

1. `parec` читает monitor default sink как mono 16 kHz PCM.
2. Поток разбивается на короткие FFT-окна.
3. Выбираются выраженные спектральные пики.
4. Пары пиков превращаются в hashes `(freq1, freq2, delta_time)`.
5. В live-аудио ищется множество hashes с одним временным сдвигом относительно reference.
6. Когда один offset набирает достаточно votes, выполняется action.

Это landmark/fingerprint-поиск, поэтому посторонние звуки обычно добавляют лишние пики, но не уничтожают согласованный рисунок target.
