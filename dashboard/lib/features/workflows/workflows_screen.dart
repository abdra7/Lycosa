import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/session.dart';
import 'providers.dart';
import 'run_screen.dart';

class WorkflowsScreen extends ConsumerWidget {
  const WorkflowsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final workflows = ref.watch(workflowsProvider);
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('Workflows', style: Theme.of(context).textTheme.headlineSmall),
              const Spacer(),
              IconButton(
                tooltip: 'Refresh',
                icon: const Icon(Icons.refresh),
                onPressed: () => ref.invalidate(workflowsProvider),
              ),
              FilledButton.icon(
                icon: const Icon(Icons.add),
                label: const Text('New workflow'),
                onPressed: () => showDialog(
                  context: context,
                  builder: (_) => const CreateWorkflowDialog(),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Expanded(
            child: workflows.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => Center(child: Text('Failed to load: $e')),
              data: (list) => list.isEmpty
                  ? const Center(child: Text('No workflows defined yet.'))
                  : ListView(
                      children: [
                        for (final workflow in list)
                          Card(
                            child: ListTile(
                              leading: const Icon(Icons.account_tree_outlined),
                              title: Text(workflow.name),
                              subtitle: Text(workflow.description ??
                                  '${(workflow.definition['steps'] as List?)?.length ?? 0} steps'),
                              trailing: FilledButton.tonalIcon(
                                icon: const Icon(Icons.play_arrow),
                                label: const Text('Run'),
                                onPressed: () => _promptAndRun(context, workflow),
                              ),
                            ),
                          ),
                      ],
                    ),
            ),
          ),
        ],
      ),
    );
  }

  void _promptAndRun(BuildContext context, WorkflowInfo workflow) {
    showDialog(
      context: context,
      builder: (_) => _RunInputDialog(workflow: workflow),
    );
  }
}

class _RunInputDialog extends ConsumerStatefulWidget {
  const _RunInputDialog({required this.workflow});

  final WorkflowInfo workflow;

  @override
  ConsumerState<_RunInputDialog> createState() => _RunInputDialogState();
}

class _RunInputDialogState extends ConsumerState<_RunInputDialog> {
  final _input = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  Future<void> _run() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null || _input.text.trim().isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final run = await client.runWorkflow(widget.workflow.id, _input.text.trim());
      if (mounted) {
        Navigator.of(context).pop();
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => RunScreen(
              workflowName: widget.workflow.name,
              workflowId: run.workflowId,
              runId: run.id),
        ));
      }
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } on ControllerUnreachableException catch (e) {
      setState(() => _error = e.friendly);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text('Run ${widget.workflow.name}'),
      content: SizedBox(
        width: 480,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: _input,
              maxLines: 3,
              minLines: 1,
              autofocus: true,
              decoration: const InputDecoration(labelText: 'Input'),
            ),
            if (_busy)
              const Padding(
                padding: EdgeInsets.only(top: 12),
                child: LinearProgressIndicator(),
              ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(_error!,
                    style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel')),
        FilledButton(
          onPressed: _busy ? null : _run,
          child: const Text('Run'),
        ),
      ],
    );
  }
}

const _definitionTemplate = '''
{
  "steps": [
    {"id": "plan", "kind": "task", "prompt": "Plan how to: {{input}}"},
    {"id": "gate", "kind": "approval", "message": "Review the plan"},
    {"id": "do", "kind": "task", "prompt": "Execute:\\n{{steps.plan.output}}"}
  ]
}''';

class CreateWorkflowDialog extends ConsumerStatefulWidget {
  const CreateWorkflowDialog({super.key});

  @override
  ConsumerState<CreateWorkflowDialog> createState() =>
      _CreateWorkflowDialogState();
}

class _CreateWorkflowDialogState extends ConsumerState<CreateWorkflowDialog> {
  final _name = TextEditingController();
  final _description = TextEditingController();
  final _definition = TextEditingController(text: _definitionTemplate);
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    for (final c in [_name, _description, _definition]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _create() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final definition =
          jsonDecode(_definition.text) as Map<String, dynamic>;
      await client.createWorkflow(
        name: _name.text.trim(),
        description:
            _description.text.trim().isEmpty ? null : _description.text.trim(),
        definition: definition,
      );
      ref.invalidate(workflowsProvider);
      if (mounted) Navigator.of(context).pop();
    } on FormatException catch (e) {
      setState(() => _error = 'Definition is not valid JSON: ${e.message}');
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } on ControllerUnreachableException catch (e) {
      setState(() => _error = e.friendly);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('New workflow'),
      content: SizedBox(
        width: 640,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: _name,
              decoration: const InputDecoration(labelText: 'Name'),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _description,
              decoration:
                  const InputDecoration(labelText: 'Description (optional)'),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _definition,
              maxLines: 14,
              style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
              decoration: const InputDecoration(
                labelText: 'Definition (JSON)',
                alignLabelWithHint: true,
                border: OutlineInputBorder(),
              ),
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(_error!,
                    style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel')),
        FilledButton(
          onPressed: _busy ? null : _create,
          child: _busy
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : const Text('Create'),
        ),
      ],
    );
  }
}
