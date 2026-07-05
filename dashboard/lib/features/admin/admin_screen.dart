import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/session.dart';

final auditLogsProvider =
    FutureProvider.autoDispose<List<AuditLogEntry>>((ref) async {
  final client = ref.watch(activeApiClientProvider);
  if (client == null) return const [];
  return client.listAuditLogs(limit: 100);
});

final apiKeysProvider =
    FutureProvider.autoDispose<List<ApiKeyInfo>>((ref) async {
  final client = ref.watch(activeApiClientProvider);
  if (client == null) return const [];
  return client.listApiKeys();
});

class AdminScreen extends ConsumerWidget {
  const AdminScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final principal = ref.watch(sessionProvider).value?.principal;
    if (principal?.role != 'admin') {
      return const Center(
          child: Text('This section requires the admin role.'));
    }
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('Admin', style: Theme.of(context).textTheme.headlineSmall),
              const Spacer(),
              IconButton(
                tooltip: 'Refresh',
                icon: const Icon(Icons.refresh),
                onPressed: () {
                  ref.invalidate(auditLogsProvider);
                  ref.invalidate(apiKeysProvider);
                },
              ),
            ],
          ),
          const SizedBox(height: 8),
          const Expanded(flex: 2, child: _ApiKeysCard()),
          const SizedBox(height: 12),
          const Expanded(flex: 3, child: _AuditCard()),
        ],
      ),
    );
  }
}

class _ApiKeysCard extends ConsumerWidget {
  const _ApiKeysCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final keys = ref.watch(apiKeysProvider);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('API keys', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Expanded(
              child: keys.when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (e, _) => Text('Failed: $e'),
                data: (list) => list.isEmpty
                    ? const Text('No API keys.')
                    : ListView(
                        children: [
                          for (final key in list)
                            ListTile(
                              dense: true,
                              leading: Icon(
                                key.isRevoked
                                    ? Icons.key_off
                                    : Icons.key,
                                color: key.isRevoked
                                    ? Theme.of(context).colorScheme.error
                                    : null,
                              ),
                              title: Text('${key.name} (${key.keyPrefix}…)'),
                              subtitle: Text([
                                if (key.isRevoked) 'revoked',
                                if (key.nodeId != null) 'bound to node',
                                if (key.lastUsedAt != null)
                                  'last used ${key.lastUsedAt!.toLocal()}'
                              ].join(' · ')),
                              trailing: key.isRevoked
                                  ? null
                                  : TextButton(
                                      onPressed: () =>
                                          _revoke(context, ref, key),
                                      child: const Text('Revoke'),
                                    ),
                            ),
                        ],
                      ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _revoke(
      BuildContext context, WidgetRef ref, ApiKeyInfo key) async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    try {
      await client.revokeApiKey(key.id);
      ref.invalidate(apiKeysProvider);
      ref.invalidate(auditLogsProvider);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Revoked ${key.name}')));
      }
    } on ApiException catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.friendly)));
      }
    }
  }
}

class _AuditCard extends ConsumerWidget {
  const _AuditCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final logs = ref.watch(auditLogsProvider);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Audit log', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Expanded(
              child: logs.when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (e, _) => Text('Failed: $e'),
                data: (list) => list.isEmpty
                    ? const Text('No audit entries.')
                    : SingleChildScrollView(
                        child: DataTable(
                          dataRowMinHeight: 32,
                          dataRowMaxHeight: 40,
                          columns: const [
                            DataColumn(label: Text('Time')),
                            DataColumn(label: Text('Action')),
                            DataColumn(label: Text('Actor')),
                            DataColumn(label: Text('Resource')),
                            DataColumn(label: Text('IP')),
                          ],
                          rows: [
                            for (final entry in list)
                              DataRow(cells: [
                                DataCell(Text(entry.createdAt
                                    .toLocal()
                                    .toString()
                                    .substring(0, 19))),
                                DataCell(Text(entry.action)),
                                DataCell(Text(entry.actor)),
                                DataCell(Text(entry.resourceType ?? '—')),
                                DataCell(Text(entry.ipAddress ?? '—')),
                              ]),
                          ],
                        ),
                      ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
