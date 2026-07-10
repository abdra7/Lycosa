import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/session.dart';

final workflowsProvider = FutureProvider.autoDispose<List<WorkflowInfo>>((
  ref,
) async {
  final client = ref.watch(activeApiClientProvider);
  if (client == null) return const [];
  return client.listWorkflows();
});

typedef RunKey = ({String workflowId, String runId});

/// One workflow run, polled every 3 s while running/paused so approvals
/// and step progress show up live; polling stops once the run finishes.
final runProvider = StreamProvider.autoDispose.family<WorkflowRunInfo, RunKey>((
  ref,
  key,
) {
  final client = ref.watch(activeApiClientProvider);
  final controller = StreamController<WorkflowRunInfo>();
  if (client == null) {
    ref.onDispose(controller.close);
    return controller.stream;
  }

  Timer? timer;
  Future<void> tick() async {
    try {
      final run = await client.getRun(key.workflowId, key.runId);
      if (controller.isClosed) return;
      controller.add(run);
      if (run.isFinished) timer?.cancel();
    } catch (error, stack) {
      if (!controller.isClosed) controller.addError(error, stack);
    }
  }

  tick();
  timer = Timer.periodic(const Duration(seconds: 3), (_) => tick());
  ref.onDispose(() {
    timer?.cancel();
    controller.close();
  });
  return controller.stream;
});
