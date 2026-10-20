(() => {
  const video = document.querySelector('#screen');
  const empty = document.querySelector('#empty');
  const splashImage = document.querySelector('#splashImage');
  const emptyTitle = document.querySelector('#emptyTitle');
  const emptyText = document.querySelector('#emptyText');
  const state = document.querySelector('#state');
  const stateText = document.querySelector('#stateText');
  const encoder = document.querySelector('#encoder');
  const latency = document.querySelector('#latency');
  const clients = document.querySelector('#clients');
  const vlcLink = document.querySelector('#vlcLink');
  const vlcUrl = document.querySelector('#vlcUrl');
  const fullscreen = document.querySelector('#fullscreen');
  const reconnect = document.querySelector('#reconnect');
  const sound = document.querySelector('#sound');
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token') || '';
  const query = token ? `?token=${encodeURIComponent(token)}` : '';
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const socketUrl = `${scheme}://${window.location.host}/ws${query}`;
  const externalUrl = `${window.location.origin}/vlc.mp4${query}`;
  const hlsUrl = `${window.location.origin}/hls/index.m3u8${query}`;
  // Принудительный выбор источника: ?source=auto|hls|direct — пригодится,
  // когда конкретному устройству не подходит автовыбор.
  const forcedSource = ['auto', 'hls', 'direct'].includes(params.get('source'))
    ? params.get('source')
    : 'auto';
  // HLS в <video> нативно играет только WebKit (iOS/macOS Safari). Chrome, включая
  // Android, отвечает canPlayType('application/vnd.apple.mpegurl') = 'maybe', но поток
  // не показывает, поэтому доверяем только user agent.
  const ua = navigator.userAgent;
  const isWebKitPlayer =
    /iP(hone|ad|od)/.test(ua) ||
    (/Safari/.test(ua) && !/Chrome|Chromium|CriOS|Edg|OPR|Android|FxiOS/.test(ua));

  function shouldUseHls() {
    if (forcedSource === 'hls') return true;
    if (forcedSource === 'direct') return false;
    return isWebKitPlayer;
  }
  const translations = {
    ru: {
      eyebrow: 'REMOTE DESKTOP / LOW LATENCY',
      encoder_label: 'Кодировщик',
      latency_label: 'Задержка буфера',
      clients_label: 'Клиенты',
      fullscreen: 'Во весь экран',
      reconnect: 'Переподключить',
      sound_off: 'Включить звук',
      sound_on: 'Выключить звук',
      external_player: 'EXTERNAL PLAYER',
      vlc_title: 'Поток для VLC',
      vlc_open: 'Открыть URL потока',
      hint: 'Если браузер не запускает звук, нажмите «Включить звук» или Play. Для VLC используйте ссылку выше; токен уже встроен.',
      connecting: 'Подключение',
      waiting_stream: 'Ожидание трансляции',
      server_working: 'Сервер работает. Экран появится с первым кадром.',
      live: 'В эфире',
      no_media_source: 'Браузер не поддерживает MediaSource',
      no_codec: 'Нет {codec} в браузере',
      mse_unavailable: 'MSE недоступен',
      direct_unavailable: 'Прямой поток недоступен',
      tap_to_play: 'Нажмите на экран — включится звук',
      play_error: 'Не удалось запустить воспроизведение',
      waiting_frame: 'Ожидание кадра',
      socket_error: 'Ошибка сокета',
      reconnecting: 'Переподключение',
      ffmpeg_error: 'Ошибка FFmpeg',
      ffmpeg_reconnecting: 'Переподключение FFmpeg',
      stream_disabled: 'Трансляция отключена',
      disabled_title: 'Трансляция отключена',
      disabled_text: 'FFmpeg выключен. На экране показана заставка.',
      splash_alt: 'Заставка трансляции'
    },
    uk: {
      eyebrow: 'REMOTE DESKTOP / LOW LATENCY',
      encoder_label: 'Кодувальник',
      latency_label: 'Затримка буфера',
      clients_label: 'Клієнти',
      fullscreen: 'На весь екран',
      reconnect: 'Перепідключити',
      sound_off: 'Увімкнути звук',
      sound_on: 'Вимкнути звук',
      external_player: 'EXTERNAL PLAYER',
      vlc_title: 'Потік для VLC',
      vlc_open: 'Відкрити URL потоку',
      hint: 'Якщо браузер не запускає звук, натисніть «Увімкнути звук» або Play. Для VLC використовуйте посилання вище; токен уже вбудовано.',
      connecting: 'Підключення',
      waiting_stream: 'Очікування трансляції',
      server_working: 'Сервер працює. Екран з\'явиться з першим кадром.',
      live: 'У ефірі',
      no_media_source: 'Браузер не підтримує MediaSource',
      no_codec: 'Немає {codec} у браузері',
      mse_unavailable: 'MSE недоступний',
      direct_unavailable: 'Прямий поток недоступний',
      tap_to_play: 'Натисніть на екран — увімкнеться звук',
      play_error: 'Не вдалося запустити відтворення',
      waiting_frame: 'Очікування кадру',
      socket_error: 'Помилка сокета',
      reconnecting: 'Перепідключення',
      ffmpeg_error: 'Помилка FFmpeg',
      ffmpeg_reconnecting: 'Перепідключення FFmpeg',
      stream_disabled: 'Трансляція вимкнена',
      disabled_title: 'Трансляція вимкнена',
      disabled_text: 'FFmpeg вимкнено. На екрані показано заставку.',
      splash_alt: 'Заставка трансляції'
    },
    en: {
      eyebrow: 'REMOTE DESKTOP / LOW LATENCY',
      encoder_label: 'Encoder',
      latency_label: 'Buffer latency',
      clients_label: 'Clients',
      fullscreen: 'Fullscreen',
      reconnect: 'Reconnect',
      sound_off: 'Enable sound',
      sound_on: 'Mute sound',
      external_player: 'EXTERNAL PLAYER',
      vlc_title: 'Stream for VLC',
      vlc_open: 'Open stream URL',
      hint: 'If the browser does not start audio, press Enable sound or Play. For VLC use the link above; the token is already included.',
      connecting: 'Connecting',
      waiting_stream: 'Waiting for stream',
      server_working: 'The server is running. The screen will appear with the first frame.',
      live: 'Live',
      no_media_source: 'The browser does not support MediaSource',
      no_codec: '{codec} is not available in the browser',
      mse_unavailable: 'MSE is unavailable',
      direct_unavailable: 'Direct stream is unavailable',
      tap_to_play: 'Tap the screen to enable sound',
      play_error: 'Could not start playback',
      waiting_frame: 'Waiting for frame',
      socket_error: 'Socket error',
      reconnecting: 'Reconnecting',
      ffmpeg_error: 'FFmpeg error',
      ffmpeg_reconnecting: 'Reconnecting FFmpeg',
      stream_disabled: 'Stream disabled',
      disabled_title: 'Stream disabled',
      disabled_text: 'FFmpeg is off. A splash screen is shown.',
      splash_alt: 'Stream splash screen'
    },
    be: {
      eyebrow: 'REMOTE DESKTOP / LOW LATENCY',
      encoder_label: 'Кадарвае кольцаванне',
      latency_label: 'Затрымка буфера',
      clients_label: 'Кліенты',
      fullscreen: 'На ўвесь экран',
      reconnect: 'Перападключыць',
      sound_off: 'Уключыць гук',
      sound_on: 'Выключыць гук',
      external_player: 'EXTERNAL PLAYER',
      vlc_title: 'Паток для VLC',
      vlc_open: 'Адкрыць URL патока',
      hint: 'Калі браўзер не запускае гук, націсніце «Уключыць гук» або Play. Для VLC выкарыстоўвайце спасылку вышэй; токен ужо ўбудаваны.',
      connecting: 'Падключэнне',
      waiting_stream: 'Чаканне трансляцыі',
      server_working: 'Сервер працуе. Экран з\'явіцца з першым кадрам.',
      live: 'У эфіры',
      no_media_source: 'Браўзер не падтрымлівае MediaSource',
      no_codec: 'Няма {codec} у браўзеры',
      mse_unavailable: 'MSE недаступны',
      direct_unavailable: 'Прамы струм недаступны',
      tap_to_play: 'Націсніце на экран — уключыцца гук',
      play_error: 'Не ўдалося запусціць адтворенне',
      waiting_frame: 'Чаканне кадра',
      socket_error: 'Памылка сокета',
      reconnecting: 'Перападключэнне',
      ffmpeg_error: 'Памылка FFmpeg',
      ffmpeg_reconnecting: 'Перападключэнне FFmpeg',
      stream_disabled: 'Трансляцыя выключана',
      disabled_title: 'Трансляцыя выключана',
      disabled_text: 'FFmpeg спынены. На экране паказана застава.',
      splash_alt: 'Застава трансляцыі'
    }
  };
  let language = 'ru';
  let videoCodec = 'h264';
  let streamEnabled = true;
  let socket = null;
  let mediaSource = null;
  let sourceBuffer = null;
  let objectUrl = null;
  let pending = [];
  let reconnectTimer = null;
  let fallbackTimer = null;
  let usingFallback = false;
  let stopping = false;
  let audioEnabled = true;
  let currentStateKey = 'connecting';
  let currentStateKind = '';
  let currentStateValues = {};
  let autoplayBlocked = false;
  let currentSource = 'mse';

  // Мобильные браузеры (iOS Safari, Android Chrome) запрещают автозапуск со звуком.
  // Это не «поток недоступен»: нужен один жест пользователя, чтобы включить звук.
  function handlePlayRejection(error) {
    if (error && error.name === 'NotAllowedError') {
      autoplayBlocked = true;
      setState('tap_to_play', 'warn');
      return;
    }
    if (currentSource === 'direct') setState('direct_unavailable', 'error');
    else if (currentSource === 'hls') setState('play_error', 'error');
    else setState('mse_unavailable', 'error');
  }

  function startPlayback() {
    const attempt = video.play();
    if (attempt && attempt.catch) attempt.catch(handlePlayRejection);
  }

  function unlockPlayback() {
    if (!autoplayBlocked) return;
    if (video.muted) {
      video.muted = false;
      updateSoundButton();
    }
    autoplayBlocked = false;
    startPlayback();
  }

  function t(key) {
    return translations[language][key] ?? translations.ru[key] ?? key;
  }

  function formatted(key, values = {}) {
    return Object.entries(values).reduce((text, [name, value]) => text.replace(`{${name}}`, value), t(key));
  }

  function applyLanguage(nextLanguage) {
    language = translations[nextLanguage] ? nextLanguage : 'ru';
    document.documentElement.lang = language;
    document.querySelectorAll('[data-i18n]').forEach(element => {
      const key = element.dataset.i18n;
      if (translations[language][key]) element.textContent = translations[language][key];
    });
    splashImage.alt = t('splash_alt');
    stateText.textContent = formatted(currentStateKey, currentStateValues);
    state.className = `state ${currentStateKind}`.trim();
    updateSoundButton();
  }

  function updateSoundButton() {
    sound.textContent = t(video.muted ? 'sound_off' : 'sound_on');
  }

  function setState(key, kind = '', values = {}) {
    currentStateKey = key;
    currentStateKind = kind;
    currentStateValues = values;
    stateText.textContent = formatted(key, values);
    state.className = `state ${kind}`.trim();
  }

  function setPlaceholder(titleKey, textKey) {
    emptyTitle.textContent = t(titleKey);
    emptyText.textContent = t(textKey);
    empty.classList.remove('hidden');
  }

  function setSplash() {
    splashImage.src = `/splash${query}`;
  }

  function codecCandidates() {
    const audio = audioEnabled ? ',mp4a.40.2' : '';
    if (videoCodec === 'hevc') {
      return [
        `video/mp4; codecs="hvc1.1.6.L93.B0${audio}"`,
        `video/mp4; codecs="hev1.1.6.L93.B0${audio}"`
      ];
    }
    return [
      `video/mp4; codecs="avc1.640033${audio}"`,
      `video/mp4; codecs="avc1.640029${audio}"`,
      `video/mp4; codecs="avc1.42E01E${audio}"`,
      `video/mp4; codecs="avc1.640033,mp4a.40.2"`,
      `video/mp4; codecs="avc1.640029,mp4a.40.2"`,
      'video/mp4'
    ];
  }

  function trimBuffer() {
    if (!sourceBuffer || !sourceBuffer.buffered.length) return;
    const end = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1);
    const start = sourceBuffer.buffered.start(0);
    const current = video.currentTime;
    const cut = Math.min(end - 1, Math.max(start, current - 3));
    if (current > 0.5 && end - current > 1.2 && !sourceBuffer.updating) {
      try { sourceBuffer.remove(start, cut); } catch (_) {}
    }
  }

  function drainPending() {
    if (!sourceBuffer || sourceBuffer.updating || pending.length === 0) return;
    const data = pending.shift();
    try {
      sourceBuffer.appendBuffer(data);
    } catch (error) {
      pending = [];
      reconnectPlayer();
    }
  }

  function append(data) {
    if (!sourceBuffer) {
      pending.push(data);
      return;
    }
    if (sourceBuffer.updating) {
      pending.push(data);
      if (pending.length > 8) pending.splice(0, pending.length - 8);
      return;
    }
    try {
      sourceBuffer.appendBuffer(data);
      if (data.byteLength > 100) {
        if (fallbackTimer) {
          window.clearTimeout(fallbackTimer);
          fallbackTimer = null;
        }
        empty.classList.add('hidden');
        setState('live', 'ready');
        video.play().catch(() => {});
      }
    } catch (error) {
      reconnectPlayer();
    }
  }

  function closeSocket() {
    if (socket) {
      socket.onclose = null;
      socket.close();
      socket = null;
    }
  }

  function cleanupPlayer() {
    if (fallbackTimer) {
      window.clearTimeout(fallbackTimer);
      fallbackTimer = null;
    }
    usingFallback = false;
    closeSocket();
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
    if (mediaSource && mediaSource.readyState === 'open') {
      try { mediaSource.endOfStream(); } catch (_) {}
    }
    mediaSource = null;
    sourceBuffer = null;
    pending = [];
    empty.classList.remove('hidden');
  }

  function showDisabled() {
    cleanupPlayer();
    setSplash();
    setPlaceholder('disabled_title', 'disabled_text');
    setState('stream_disabled', 'disabled');
    encoder.textContent = '—';
  }

  function startFallback(force) {
    if (!streamEnabled) return;
    if (!force && usingFallback && video.currentSrc === externalUrl && !video.error) return;
    cleanupPlayer();
    usingFallback = true;
    currentSource = 'direct';
    video.src = externalUrl;
    video.load();
    empty.classList.add('hidden');
    setState('live', 'ready');
    autoplayBlocked = false;
    startPlayback();
  }

  // HLS — нативный путь для iPhone/iPad и Safari. Там нет MSE, а бесконечный
  // chunked-MP4 играет через раз, поэтому для WebKit источник выбирается сразу.
  function startHls(force) {
    if (!streamEnabled) return;
    if (!force && usingFallback && video.currentSrc === hlsUrl && !video.error) return;
    cleanupPlayer();
    usingFallback = true;
    currentSource = 'hls';
    video.src = hlsUrl;
    video.load();
    empty.classList.add('hidden');
    setState('live', 'ready');
    autoplayBlocked = false;
    startPlayback();
  }

  function connectPlayer() {
    stopping = false;
    if (!streamEnabled) {
      showDisabled();
      return;
    }
    if (shouldUseHls()) {
      startHls();
      return;
    }
    if (forcedSource === 'direct') {
      startFallback(true);
      return;
    }
    connectMSEPlayer();
  }

  function connectMSEPlayer() {
    stopping = false;
    cleanupPlayer();
    currentSource = 'mse';
    if (!streamEnabled) {
      showDisabled();
      return;
    }
    setSplash();
    setPlaceholder('waiting_stream', 'server_working');
    if (!window.MediaSource) {
      startFallback();
      return;
    }
    mediaSource = new MediaSource();
    objectUrl = URL.createObjectURL(mediaSource);
    video.src = objectUrl;
    fallbackTimer = window.setTimeout(() => {
      if (!sourceBuffer || !sourceBuffer.buffered.length) startFallback();
    }, 8000);
    mediaSource.addEventListener('sourceopen', () => {
      const mime = codecCandidates().find(codec => MediaSource.isTypeSupported(codec));
      if (!mime) {
        setState('no_codec', 'error', { codec: videoCodec.toUpperCase() });
        return;
      }
      try {
        sourceBuffer = mediaSource.addSourceBuffer(mime);
        sourceBuffer.mode = 'sequence';
        sourceBuffer.addEventListener('updateend', () => {
          trimBuffer();
          drainPending();
        });
        sourceBuffer.addEventListener('error', () => {
          setState('mse_unavailable', 'error');
          startFallback();
        });
      } catch (error) {
        setState('mse_unavailable', 'error');
        startFallback();
        return;
      }
      socket = new WebSocket(socketUrl);
      socket.binaryType = 'arraybuffer';
      socket.onopen = () => setState('waiting_frame');
      socket.onmessage = event => {
        if (!(event.data instanceof ArrayBuffer)) return;
        append(event.data);
      };
      socket.onerror = () => setState('socket_error', 'error');
      socket.onclose = () => {
        if (stopping || !streamEnabled) return;
        if (usingFallback) {
          startFallback(true);
          return;
        }
        setState('reconnecting');
        reconnectTimer = window.setTimeout(connectPlayer, 1000);
      };
    }, { once: true });
  }

  function reconnectPlayer() {
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    if (!streamEnabled) {
      showDisabled();
      return;
    }
    cleanupPlayer();
    window.setTimeout(() => connectPlayer(), 100);
  }

  async function updateStatus() {
    try {
      const response = await fetch(`/api/status${query}`, { cache: 'no-store' });
      if (!response.ok) return null;
      const status = await response.json();
      applyLanguage(status.ui_language || 'ru');
      const nextEnabled = status.stream_enabled !== false;
      if (nextEnabled !== streamEnabled) {
        streamEnabled = nextEnabled;
        if (streamEnabled) connectPlayer();
        else showDisabled();
      }
      const nextCodec = status.video_codec || videoCodec;
      const nextAudio = status.audio_enabled !== false;
      if (nextCodec !== videoCodec || nextAudio !== audioEnabled) {
        const codecChanged = Boolean(mediaSource) || nextAudio !== audioEnabled;
        videoCodec = nextCodec;
        audioEnabled = nextAudio;
        if (codecChanged) reconnectPlayer();
      }
      encoder.textContent = status.active_encoder || t('waiting_stream');
      clients.textContent = String(status.clients ?? 0);
      if (!streamEnabled || status.state === 'disabled') {
        showDisabled();
      } else if (status.state === 'error') {
        setState('ffmpeg_error', 'error');
      } else if (status.state === 'reconnecting') {
        setState('ffmpeg_reconnecting');
      }
      return status;
    } catch (_) {
      return null;
    }
  }

  function updateLatency() {
    const ranges = sourceBuffer && sourceBuffer.buffered.length ? sourceBuffer.buffered : video.buffered;
    if (ranges.length) {
      const end = ranges.end(ranges.length - 1);
      const value = Math.max(0, end - video.currentTime);
      latency.textContent = `${value.toFixed(2)} s`;
    } else {
      latency.textContent = '—';
    }
    window.requestAnimationFrame(updateLatency);
  }

  fullscreen.addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else video.parentElement.requestFullscreen?.();
  });
  reconnect.addEventListener('click', reconnectPlayer);
  sound.addEventListener('click', () => {
    video.muted = !video.muted;
    video.play().catch(() => {});
    updateSoundButton();
  });
  video.addEventListener('volumechange', updateSoundButton);
  video.addEventListener('playing', () => {
    if (usingFallback) {
      empty.classList.add('hidden');
      setState('live', 'ready');
    }
  });
  video.addEventListener('error', () => {
    if (autoplayBlocked) return;
    if (!usingFallback) return;
    setState(currentSource === 'hls' ? 'play_error' : 'direct_unavailable', 'error');
  });
  ['pointerdown', 'touchend', 'click', 'keydown'].forEach(type => {
    window.addEventListener(type, unlockPlayback, { passive: true });
  });
  vlcLink.href = externalUrl;
  vlcUrl.textContent = externalUrl;
  applyLanguage(language);
  updateStatus().finally(() => connectPlayer());
  window.setInterval(updateStatus, 2000);
  updateLatency();
})();
