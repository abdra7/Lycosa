import 'dart:io' show InternetAddressType, NetworkInterface, Platform;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/brand.dart';
import '../../core/session.dart';

/// True when [url]'s host is a loopback name — reachable only on the machine
/// running the dashboard, never from another device.
bool isLoopbackHost(String url) {
  final host = Uri.tryParse(url)?.host;
  return host == 'localhost' || host == '127.0.0.1';
}

/// Rewrites a loopback controller URL to use [lanIp] so agents on other devices
/// can reach it. Returns [url] unchanged when it isn't loopback or no IP was
/// detected.
String controllerUrlForOtherDevices(String url, String? lanIp) {
  if (lanIp == null || !isLoopbackHost(url)) return url;
  return url.replaceFirst(Uri.parse(url).host, lanIp);
}

/// Picks the most likely real LAN IPv4 from [addresses], skipping VirtualBox
/// (192.168.56.x), Docker/WSL (172.16-31.x) and link-local (169.254.x), and
/// preferring 192.168.x over 10.x over anything else. Null if none qualify.
String? bestLanIpv4(Iterable<String> addresses) {
  final candidates = addresses
      .where(
        (ip) =>
            !ip.startsWith('169.254.') &&
            !ip.startsWith('192.168.56.') &&
            !_isDockerOrWslRange(ip),
      )
      .toList();
  candidates.sort((a, b) => _lanRank(a).compareTo(_lanRank(b)));
  return candidates.isEmpty ? null : candidates.first;
}

bool _isDockerOrWslRange(String ip) {
  final parts = ip.split('.');
  if (parts.length != 4 || parts[0] != '172') return false;
  final second = int.tryParse(parts[1]) ?? 0;
  return second >= 16 && second <= 31;
}

int _lanRank(String ip) {
  if (ip.startsWith('192.168.')) return 0;
  if (ip.startsWith('10.')) return 1;
  return 2;
}

/// Admin flow: mint a node API key and hand the operator the exact
/// command to run on the new machine. The key is shown exactly once.
class AddNodeDialog extends ConsumerStatefulWidget {
  const AddNodeDialog({super.key});

  @override
  ConsumerState<AddNodeDialog> createState() => _AddNodeDialogState();
}

class _AddNodeDialogState extends ConsumerState<AddNodeDialog> {
  final _name = TextEditingController();
  MintedApiKey? _minted;
  String? _error;
  bool _busy = false;
  String? _lanIp; // this controller host's LAN IP, when the URL is localhost

  @override
  void initState() {
    super.initState();
    _detectLanIpIfLocalhost();
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  /// When the dashboard is connected to the controller over localhost, it is
  /// running ON the controller host — so this machine's own LAN IP is the
  /// address other devices must use. Detect it so the generated command never
  /// hands out an unreachable "localhost" to copy onto another machine.
  Future<void> _detectLanIpIfLocalhost() async {
    if (!isLoopbackHost(_configuredBaseUrl)) return;
    final ip = await _detectLanIpv4();
    if (mounted && ip != null) setState(() => _lanIp = ip);
  }

  static Future<String?> _detectLanIpv4() async {
    try {
      final interfaces = await NetworkInterface.list(
        type: InternetAddressType.IPv4,
        includeLoopback: false,
        includeLinkLocal: false,
      );
      return bestLanIpv4([
        for (final iface in interfaces)
          for (final addr in iface.addresses) addr.address,
      ]);
    } catch (_) {
      return null; // fall back to showing the configured URL as-is
    }
  }

  Future<void> _mint() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null || _name.text.trim().isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final minted = await client.createNodeApiKey(_name.text.trim());
      setState(() => _minted = minted);
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // The dashboard is a desktop app, so default the shown commands to the OS the
  // operator is on — but agents can target any OS, so both forms are offered.
  bool get _windows => Platform.isWindows;

  static const _installPowershell =
      'irm https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.ps1 | iex';
  static const _installBash =
      'curl -fsSL https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.sh | bash';

  String get _configuredBaseUrl =>
      ref.read(sessionProvider).value?.activeProfile?.baseUrl ??
      'http://<controller>:8000';

  bool get _hostIsLocalhost => isLoopbackHost(_configuredBaseUrl);

  /// The URL to show agents on OTHER machines: the configured one, but with a
  /// localhost host swapped for this controller's detected LAN IP.
  String get _baseUrl => controllerUrlForOtherDevices(_configuredBaseUrl, _lanIp);

  // PowerShell: each var on its own line, no line continuations.
  String get _runPowershell =>
      '\$env:LYCOSA_CONTROLLER_URL = "$_baseUrl"\n'
      '\$env:LYCOSA_API_KEY = "${_minted?.apiKey}"\n'
      'lycosa-agent run';

  // bash: env assignments with backslash continuations.
  String get _runBash =>
      'LYCOSA_CONTROLLER_URL=$_baseUrl \\\n'
      'LYCOSA_API_KEY=${_minted?.apiKey} \\\n'
      'lycosa-agent run';

  String get _installCommand => _windows ? _installPowershell : _installBash;
  String get _command => _windows ? _runPowershell : _runBash;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Add a node'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: _minted == null ? _mintForm() : _keyReveal(context),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(_minted == null ? 'Cancel' : 'Done'),
        ),
        if (_minted == null)
          FilledButton(
            onPressed: _busy ? null : _mint,
            child: _busy
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Create key'),
          ),
      ],
    );
  }

  Widget _mintForm() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Mint a node API key. Install the agent on the machine, '
          'then run it with this key — the node registers itself.',
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _name,
          decoration: const InputDecoration(
            labelText: 'Key name',
            hintText: 'garage-workstation',
          ),
          onSubmitted: (_) => _mint(),
        ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
      ],
    );
  }

  Widget _keyReveal(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Icon(Icons.warning_amber, color: LycosaColors.warning),
            const SizedBox(width: 8),
            const Expanded(
              child: Text('Copy this key now — it will not be shown again.'),
            ),
          ],
        ),
        const SizedBox(height: 12),
        SelectableText(
          _minted!.apiKey,
          style: const TextStyle(fontFamily: 'monospace'),
        ),
        const SizedBox(height: 12),
        Text(
          _windows
              ? 'On the new machine (Windows PowerShell):'
              : 'On the new machine (macOS / Linux):',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 4),
        const Text('1. Install the agent (once per machine):'),
        const SizedBox(height: 4),
        _codeBlock(context, _installCommand),
        const SizedBox(height: 8),
        const Text('2. Register it and start:'),
        const SizedBox(height: 4),
        _codeBlock(context, _command, copyable: true),
        const SizedBox(height: 8),
        _urlHint(context),
      ],
    );
  }

  Widget _urlHint(BuildContext context) {
    final small = Theme.of(context).textTheme.bodySmall;
    // localhost but we could not detect a LAN IP: the URL is unreachable from
    // other machines and the operator must fix it by hand.
    if (_hostIsLocalhost && _lanIp == null) {
      return Text(
        'This URL is localhost — it only works if the agent runs on THIS '
        'controller PC. For another device, replace it with this PC\'s LAN IP '
        '(run ipconfig here).',
        style: small?.copyWith(color: LycosaColors.warning),
      );
    }
    // localhost swapped for the detected LAN IP: reassure and note the caveat.
    if (_hostIsLocalhost && _lanIp != null) {
      return Text(
        'Using this controller\'s LAN IP ($_lanIp) so other devices can reach '
        'it. Both devices must be on the same network.'
        '${_windows ? ' Paste each line separately.' : ''}',
        style: small,
      );
    }
    // an explicit host was configured: just the paste-carefully reminder.
    return Text(
      _windows
          ? 'Paste each line separately. The agent device must be able to reach '
                'this URL on your network.'
          : 'The agent device must be able to reach this URL on your network.',
      style: small,
    );
  }

  Widget _codeBlock(BuildContext context, String text, {bool copyable = false}) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.surfaceContainerLow,
            border: Border.all(color: Theme.of(context).colorScheme.outline),
            borderRadius: BorderRadius.circular(10),
          ),
          child: SelectableText(
            text,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
          ),
        ),
        if (copyable)
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              icon: const Icon(Icons.copy, size: 16),
              label: const Text('Copy'),
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: text));
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Copied to clipboard')),
                  );
                }
              },
            ),
          ),
      ],
    );
  }
}
