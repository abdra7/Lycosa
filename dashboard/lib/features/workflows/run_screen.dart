import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/session.dart';
import 'providers.dart';

class RunScreen extends ConsumerWidget {
  const RunScreen({
    super.key,
    required this.workflowName,
    required this.workflowId,
    required this.runId,
  });

  final String workflowName;
  final String workflowId;
  final String runId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final key = (workflowId: workflowId, runId: runId);
    final run = ref.watch(runProvider(key));
    return Scaffold(
      appBar: AppBar(title: Text('$workflowName — run')),
      body: run.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('Failed to load run: $e')),
        data: (r) => SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _StatusBanner(run: r),
              const SizedBox(height: 16),
              Text('Steps', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 8),
              for (final step in r.stepRuns) _StepTile(step: step),
              if (r.isPaused) ...[
                const SizedBox(height: 16),
                _ApprovalBar(runKey: key),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _StatusBanner extends StatelessWidget {
  const _StatusBanner({required this.run});

  final WorkflowRunInfo run;

  @override
  Widget build(BuildContext context) {
    final (color, icon) = switch (run.status) {
      'succeeded' => (Colors.green, Icons.check_circle),
      'failed' => (Theme.of(context).colorScheme.error, Icons.error),
      'paused' => (Colors.orange, Icons.pause_circle),
      _ => (Colors.blue, Icons.sync),
    };
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: [
            Icon(icon, color: color),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Status: ${run.status}'
                      '${run.currentStep != null && !run.isFinished ? ' (at ${run.currentStep})' : ''}'),
                  Text('Input: ${run.input}',
                      style: Theme.of(context).textTheme.bodySmall),
                  if (run.error != null)
                    Text(run.error!,
                        style: TextStyle(
                            color: Theme.of(context).colorScheme.error)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StepTile extends StatelessWidget {
  const _StepTile({required this.step});

  final StepRunInfo step;

  @override
  Widget build(BuildContext context) {
    final (color, icon) = switch (step.status) {
      'succeeded' => (Colors.green, Icons.check_circle_outline),
      'failed' => (Theme.of(context).colorScheme.error, Icons.highlight_off),
      'skipped' => (Colors.grey, Icons.redo),
      'pending_approval' => (Colors.orange, Icons.hourglass_top),
      _ => (Colors.blue, Icons.sync),
    };
    final hasBody = step.output != null || step.error != null;
    return Card(
      child: hasBody
          ? ExpansionTile(
              leading: Icon(icon, color: color),
              title: Text(step.stepId),
              subtitle: Text('${step.kind} · ${step.status}'
                  '${step.attempt > 1 ? ' · attempt ${step.attempt}' : ''}'),
              childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              expandedCrossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (step.output != null) SelectableText(step.output!),
                if (step.error != null)
                  SelectableText(step.error!,
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.error)),
              ],
            )
          : ListTile(
              leading: Icon(icon, color: color),
              title: Text(step.stepId),
              subtitle: Text('${step.kind} · ${step.status}'),
            ),
    );
  }
}

class _ApprovalBar extends ConsumerStatefulWidget {
  const _ApprovalBar({required this.runKey});

  final RunKey runKey;

  @override
  ConsumerState<_ApprovalBar> createState() => _ApprovalBarState();
}

class _ApprovalBarState extends ConsumerState<_ApprovalBar> {
  bool _busy = false;

  Future<void> _resolve(bool approved) async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    setState(() => _busy = true);
    try {
      await client.approveRun(
          widget.runKey.workflowId, widget.runKey.runId, approved);
      ref.invalidate(runProvider(widget.runKey));
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.friendly)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: [
            const Icon(Icons.pause_circle, color: Colors.orange),
            const SizedBox(width: 8),
            const Expanded(
                child: Text('This run is paused waiting for your approval.')),
            OutlinedButton(
              onPressed: _busy ? null : () => _resolve(false),
              child: const Text('Reject'),
            ),
            const SizedBox(width: 8),
            FilledButton(
              onPressed: _busy ? null : () => _resolve(true),
              child: const Text('Approve'),
            ),
          ],
        ),
      ),
    );
  }
}
