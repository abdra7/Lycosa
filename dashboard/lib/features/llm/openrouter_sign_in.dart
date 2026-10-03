import 'dart:async';
import 'dart:io';

/// Opens a URL in the user's browser. Returns false when it could not.
typedef BrowserLauncher = Future<bool> Function(String url);

/// Only OpenRouter's documented sign-in page is ever handed to the OS, and
/// it goes as a single argument to the platform URL handler, never through a
/// shell, so the URL cannot be interpreted as a command.
Future<bool> openInSystemBrowser(String url) async {
  final uri = Uri.tryParse(url);
  if (uri == null || uri.scheme != 'https' || uri.host != 'openrouter.ai') {
    return false;
  }
  try {
    if (Platform.isWindows) {
      await Process.start('rundll32', [
        'url.dll,FileProtocolHandler',
        url,
      ], mode: ProcessStartMode.detached);
    } else if (Platform.isMacOS) {
      await Process.start('open', [url], mode: ProcessStartMode.detached);
    } else {
      await Process.start('xdg-open', [url], mode: ProcessStartMode.detached);
    }
    return true;
  } on Exception {
    return false;
  }
}

/// One-shot loopback listener that receives OpenRouter's redirect
/// (`/callback?code=...`). Bound to 127.0.0.1 on a random port, so only this
/// machine can deliver a code, and closed after the first code or timeout.
class LoopbackCodeReceiver {
  HttpServer? _server;

  Future<String> start() async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server = server;
    return 'http://127.0.0.1:${server.port}/callback';
  }

  Future<String?> waitForCode({
    Duration timeout = const Duration(minutes: 5),
  }) async {
    final server = _server;
    if (server == null) return null;
    try {
      await for (final request in server.timeout(timeout)) {
        final code = request.uri.path == '/callback'
            ? request.uri.queryParameters['code']
            : null;
        request.response
          ..statusCode = code == null ? 404 : 200
          ..headers.contentType = ContentType.html
          ..write(
            code == null
                ? 'Not found'
                : '<html><body style="font-family:sans-serif">'
                      '<p>Lycosa received the OpenRouter sign-in. '
                      'You can close this window.</p></body></html>',
          );
        await request.response.close();
        if (code != null && code.isNotEmpty) return code;
      }
    } on TimeoutException {
      return null;
    } finally {
      await close();
    }
    return null;
  }

  Future<void> close() async {
    await _server?.close(force: true);
    _server = null;
  }
}
