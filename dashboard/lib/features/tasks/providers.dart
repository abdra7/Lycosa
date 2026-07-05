import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/session.dart';
import '../nodes/providers.dart' show nodePollIntervalProvider;

/// Recent tasks, polled while the tab is visible.
final tasksProvider = StreamProvider.autoDispose<List<TaskInfo>>((ref) {
  final client = ref.watch(activeApiClientProvider);
  final controller = StreamController<List<TaskInfo>>();
  if (client == null) {
    controller.add(const []);
    ref.onDispose(controller.close);
    return controller.stream;
  }

  Future<void> tick() async {
    try {
      final tasks = await client.listTasks();
      if (!controller.isClosed) controller.add(tasks);
    } catch (error, stack) {
      if (!controller.isClosed) controller.addError(error, stack);
    }
  }

  tick();
  final timer = Timer.periodic(ref.watch(nodePollIntervalProvider), (_) => tick());
  ref.onDispose(() {
    timer.cancel();
    controller.close();
  });
  return controller.stream;
});
