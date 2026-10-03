import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/brand.dart';
import '../../core/session.dart';
import 'connect_account_dialog.dart';
import 'llm_dialogs.dart';
import 'providers.dart';

/// Shown when a connected account's key is refused: account and API access
/// are separate things (consumer subscriptions are not API access).
const apiAccessUnavailableMessage =
    'Account connected\n\nAPI access is not available through this '
    'subscription.\n\nConfigure the provider\'s API access separately.';

/// AI Providers: connect accounts, test them, browse models, set the default
/// model and fallback routes (ADR-031).
class AiProvidersScreen extends ConsumerWidget {
  const AiProvidersScreen({super.key});

  void _refresh(WidgetRef ref) {
    ref.invalidate(llmAccountsProvider);
    ref.invalidate(llmRoutingProvider);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final providers = ref.watch(llmProvidersProvider);
    final accounts = ref.watch(llmAccountsProvider);
    final routing = ref.watch(llmRoutingProvider);
    final isAdmin =
        ref.watch(sessionProvider).value?.principal?.role == 'admin';

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            Text(
              'AI Providers',
              style: Theme.of(context).textTheme.headlineSmall,
            ),
            const Spacer(),
            OutlinedButton.icon(
              icon: const Icon(Icons.science_outlined),
              label: const Text('Test a route'),
              onPressed: () => showDialog<void>(
                context: context,
                builder: (_) => const PromptTestDialog(),
              ),
            ),
            const SizedBox(width: 8),
            OutlinedButton.icon(
              icon: const Icon(Icons.alt_route),
              label: const Text('Routing & fallback'),
              onPressed: accounts.hasValue && routing.value != null
                  ? () async {
                      final changed = await showDialog<bool>(
                        context: context,
                        builder: (_) => RoutingDialog(
                          accounts: accounts.value!,
                          routing: routing.value!,
                          isAdmin: isAdmin,
                        ),
                      );
                      if (changed == true) _refresh(ref);
                    }
                  : null,
            ),
            IconButton(
              tooltip: 'Refresh',
              icon: const Icon(Icons.refresh),
              onPressed: () => _refresh(ref),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          'Agents call models through Lycosa\'s LLM layer; they never see a '
          'provider or a key. Keys are encrypted on the controller.',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 12),
        _DefaultRouteCard(
          routing: routing.value,
          accounts: accounts.value ?? const [],
        ),
        const SizedBox(height: 12),
        ...switch ((providers, accounts)) {
          (AsyncError(:final error), _) || (_, AsyncError(:final error)) => [
            Text('Failed to load providers: ${_describe(error)}'),
          ],
          (AsyncData(value: final ps), AsyncData(value: final accs)) => [
            for (final p in ps)
              _ProviderCard(
                provider: p,
                accounts: accs.where((a) => a.provider == p.id).toList(),
                isAdmin: isAdmin,
                onChanged: () => _refresh(ref),
              ),
          ],
          _ => const [
            Padding(
              padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator()),
            ),
          ],
        },
      ],
    );
  }
}

String _describe(Object error) => switch (error) {
  ApiException() => error.friendly,
  ControllerUnreachableException() => error.friendly,
  _ => 'unexpected error',
};

class _DefaultRouteCard extends StatelessWidget {
  const _DefaultRouteCard({required this.routing, required this.accounts});

  final LlmRouting? routing;
  final List<LlmAccount> accounts;

  String _describeEntry(LlmRouteEntry e) {
    final account = accounts.where((a) => a.id == e.accountId).firstOrNull;
    return account == null
        ? '(removed account) · ${e.model}'
        : '${account.providerName} · ${account.label} · ${e.model}';
  }

  @override
  Widget build(BuildContext context) {
    final route =
        routing?.routeFor('default', 'personal') ??
        routing?.routeFor('default', 'deployment');
    return Card(
      child: ListTile(
        leading: const Icon(Icons.star_outline),
        title: Text(
          route == null
              ? 'No default model yet'
              : 'Default: ${_describeEntry(route.chain.first)}',
        ),
        subtitle: Text(
          route == null
              ? 'Connect an account, then pick a model under Models or '
                    'Routing & fallback.'
              : [
                  route.scope == 'deployment'
                      ? 'Deployment default'
                      : 'Your default',
                  if (route.chain.length > 1)
                    'fallbacks: ${route.chain.skip(1).map(_describeEntry).join(' → ')}',
                ].join(' · '),
        ),
      ),
    );
  }
}

class _ProviderCard extends StatelessWidget {
  const _ProviderCard({
    required this.provider,
    required this.accounts,
    required this.isAdmin,
    required this.onChanged,
  });

  final LlmProvider provider;
  final List<LlmAccount> accounts;
  final bool isAdmin;
  final VoidCallback onChanged;

  String get _kind => switch (provider.kind) {
    'local' => 'Local',
    'aggregator' => 'Aggregator',
    'compatible' => 'Compatible',
    _ => 'Cloud',
  };

  Future<void> _connect(BuildContext context) async {
    final added = await showDialog<bool>(
      context: context,
      builder: (_) =>
          ConnectAccountDialog(provider: provider, isAdmin: isAdmin),
    );
    if (added == true) onChanged();
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).textTheme.bodySmall;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(
                  provider.displayName,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(width: 8),
                Chip(visualDensity: VisualDensity.compact, label: Text(_kind)),
                const Spacer(),
                TextButton.icon(
                  icon: const Icon(Icons.add_link, size: 18),
                  label: Text(accounts.isEmpty ? 'Connect' : 'Add account'),
                  onPressed: () => _connect(context),
                ),
              ],
            ),
            CapabilityChips(provider.capabilities),
            const SizedBox(height: 4),
            if (accounts.isEmpty)
              Row(
                children: [
                  Icon(
                    Icons.circle_outlined,
                    size: 12,
                    color: LycosaColors.textSecondary,
                  ),
                  const SizedBox(width: 6),
                  Text('Not connected', style: muted),
                ],
              )
            else
              for (final account in accounts)
                _AccountRow(
                  account: account,
                  provider: provider,
                  isAdmin: isAdmin,
                  onChanged: onChanged,
                ),
          ],
        ),
      ),
    );
  }
}

class _AccountRow extends ConsumerStatefulWidget {
  const _AccountRow({
    required this.account,
    required this.provider,
    required this.isAdmin,
    required this.onChanged,
  });

  final LlmAccount account;
  final LlmProvider provider;
  final bool isAdmin;
  final VoidCallback onChanged;

  @override
  ConsumerState<_AccountRow> createState() => _AccountRowState();
}

class _AccountRowState extends ConsumerState<_AccountRow> {
  bool _busy = false;

  LlmAccount get _a => widget.account;

  Color get _dot {
    if (!_a.isActive) return LycosaColors.textSecondary;
    return switch (_a.apiAccess) {
      'available' => LycosaColors.success,
      'unavailable' => LycosaColors.error,
      _ => LycosaColors.warning,
    };
  }

  String get _accessText => switch (_a.apiAccess) {
    'available' => 'API access: available',
    'unavailable' => 'API access: unavailable',
    _ => 'API access: not tested yet',
  };

  Future<void> _test() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    setState(() => _busy = true);
    try {
      final result = await client.testLlmAccount(_a.id);
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (_) => AlertDialog(
          title: Text('Test · ${_a.label}'),
          content: Text(switch (result.status) {
            'ready' =>
              'Connected. ${result.modelsAvailable ?? 0} model(s) available'
                  '${result.latencyMs != null ? ' (${result.latencyMs} ms)' : ''}.',
            'unauthorized' =>
              '$apiAccessUnavailableMessage'
                  '${result.subscriptionNote != null ? '\n\n${result.subscriptionNote}' : ''}'
                  '${result.detail.isNotEmpty ? '\n\n${result.detail}' : ''}',
            'offline' => 'Offline: ${result.detail}',
            'unknown' => result.detail,
            _ => 'Error: ${result.detail}',
          }),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Close'),
            ),
          ],
        ),
      );
      widget.onChanged();
    } on ApiException catch (e) {
      _snack(e.friendly);
    } on ControllerUnreachableException catch (e) {
      _snack(e.friendly);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _toggle() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    try {
      await client.updateLlmAccount(
        _a.id,
        status: _a.isActive ? 'disabled' : 'active',
      );
      widget.onChanged();
    } on ApiException catch (e) {
      _snack(e.friendly);
    }
  }

  Future<void> _disconnect() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: Text('Disconnect ${_a.label}?'),
        content: const Text(
          'The stored credential is deleted and the account is removed from '
          'every route. Revoke the key at the provider as well.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Disconnect'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    try {
      await client.disconnectLlmAccount(_a.id);
      widget.onChanged();
    } on ApiException catch (e) {
      _snack(e.friendly);
    }
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    final details = [
      _a.isShared ? 'Shared' : (_a.mine ? 'Personal' : 'Another user\'s'),
      if (_a.isLocal) 'Local',
      if (_a.authMethod == 'oauth_pkce') 'Signed in',
      if (!_a.isActive) 'Disabled',
      _accessText,
    ];
    return ListTile(
      contentPadding: EdgeInsets.zero,
      dense: true,
      leading: Icon(Icons.circle, size: 12, color: _dot),
      title: Text(_a.label),
      subtitle: Text(
        [
          details.join(' · '),
          if (_a.lastTestDetail != null && _a.lastTestDetail!.isNotEmpty)
            _a.lastTestDetail!,
          if (widget.provider.takesBaseUrl) _a.baseUrl,
        ].join('\n'),
      ),
      trailing: Wrap(
        spacing: 4,
        children: [
          if (_busy)
            const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
          if (_a.usable) ...[
            TextButton(
              onPressed: _busy ? null : _test,
              child: const Text('Test'),
            ),
            TextButton(
              onPressed: () => showDialog<void>(
                context: context,
                builder: (_) => ModelsDialog(account: _a),
              ),
              child: const Text('Models'),
            ),
          ],
          if (_a.manageable)
            TextButton(
              onPressed: _toggle,
              child: Text(_a.isActive ? 'Disable' : 'Enable'),
            ),
          if (_a.manageable || widget.isAdmin)
            TextButton(onPressed: _disconnect, child: const Text('Disconnect')),
        ],
      ),
    );
  }
}
