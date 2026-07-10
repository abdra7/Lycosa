import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/app_info.dart';
import '../../core/brand.dart';
import '../../core/events.dart';
import '../../core/session.dart';
import '../../core/theme_mode.dart';
import '../../widgets/lycosa_brand.dart';
import '../admin/admin_screen.dart';
import '../knowledge/knowledge_screen.dart';
import '../nodes/nodes_screen.dart';
import '../nodes/providers.dart';
import '../tasks/tasks_screen.dart';
import '../workflows/workflows_screen.dart';

class _Section {
  const _Section(this.label, this.icon);
  final String label;
  final IconData icon;
}

const _sections = [
  _Section('Nodes', Icons.dns_outlined),
  _Section('Tasks', Icons.play_circle_outline),
  _Section('Workflows', Icons.account_tree_outlined),
  _Section('Knowledge', Icons.menu_book_outlined),
  _Section('Admin', Icons.admin_panel_settings_outlined),
];

/// Authenticated shell: nav rail + profile switcher + identity + logout,
/// plus the live event strip fed by the /events WebSocket (ADR-016).
class ShellScreen extends ConsumerStatefulWidget {
  const ShellScreen({super.key});

  @override
  ConsumerState<ShellScreen> createState() => _ShellScreenState();
}

class _ShellScreenState extends ConsumerState<ShellScreen> {
  int _selected = 0;
  LycosaEvent? _lastEvent;

  void _onEvent(AsyncValue<LycosaEvent>? _, AsyncValue<LycosaEvent> next) {
    final event = next.value;
    if (event == null) return;
    setState(() => _lastEvent = event);
    if (event.isNodeEvent) {
      // push beats polling: refresh the node list immediately
      ref.invalidate(nodesProvider);
    }
    if (event.isAlert && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(event.summary),
          backgroundColor: Theme.of(context).colorScheme.error,
          duration: const Duration(seconds: 6),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(eventsProvider, _onEvent);
    final session = ref.watch(sessionProvider).value!;
    final principal = session.principal!;
    final connected = ref.watch(eventsProvider).hasValue;

    return Scaffold(
      appBar: AppBar(
        title: const LycosaBrand(),
        actions: [
          if (session.profiles.length > 1)
            DropdownButton<String>(
              value: session.activeProfile!.id,
              underline: const SizedBox.shrink(),
              items: [
                for (final p in session.profiles)
                  DropdownMenuItem(value: p.id, child: Text(p.name)),
              ],
              onChanged: (id) {
                if (id != null) {
                  ref.read(sessionProvider.notifier).switchProfile(id);
                }
              },
            ),
          const SizedBox(width: 12),
          Chip(
            avatar: const Icon(Icons.person_outline, size: 18),
            label: Text('${principal.displayName} · ${principal.role}'),
          ),
          IconButton(
            tooltip: ref.watch(themeModeProvider) == ThemeMode.dark
                ? 'Switch to light mode'
                : 'Switch to dark mode',
            icon: Icon(
              ref.watch(themeModeProvider) == ThemeMode.dark
                  ? Icons.light_mode_outlined
                  : Icons.dark_mode_outlined,
            ),
            onPressed: () => ref.read(themeModeProvider.notifier).toggle(),
          ),
          IconButton(
            tooltip: 'Sign out',
            icon: const Icon(Icons.logout),
            onPressed: () => ref.read(sessionProvider.notifier).logout(),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: Row(
        children: [
          NavigationRail(
            selectedIndex: _selected,
            onDestinationSelected: (i) => setState(() => _selected = i),
            labelType: NavigationRailLabelType.all,
            leading: const SizedBox(height: 8),
            destinations: [
              for (final s in _sections)
                NavigationRailDestination(
                  icon: Icon(s.icon),
                  label: Text(s.label),
                ),
            ],
          ),
          const VerticalDivider(width: 1),
          Expanded(
            child: AnimatedSwitcher(
              duration: LycosaMotion.base,
              switchInCurve: LycosaMotion.curve,
              switchOutCurve: LycosaMotion.curve,
              child: switch (_selected) {
                0 => const NodesScreen(),
                1 => const TasksScreen(),
                2 => const WorkflowsScreen(),
                3 => const KnowledgeScreen(),
                _ => const AdminScreen(),
              },
            ),
          ),
        ],
      ),
      bottomNavigationBar: _EventStrip(connected: connected, event: _lastEvent),
    );
  }
}

class _EventStrip extends StatelessWidget {
  const _EventStrip({required this.connected, required this.event});

  final bool connected;
  final LycosaEvent? event;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Material(
      color: scheme.surfaceContainerLow,
      shape: Border(top: BorderSide(color: scheme.outline)),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Row(
          children: [
            AnimatedContainer(
              duration: LycosaMotion.slow,
              width: 10,
              height: 10,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: connected
                    ? LycosaColors.success
                    : LycosaColors.textSecondary,
              ),
            ),
            const SizedBox(width: 6),
            Text(
              connected ? 'live' : 'connecting…',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Text(
                event?.summary ?? '',
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            Text('v$appVersion', style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}
