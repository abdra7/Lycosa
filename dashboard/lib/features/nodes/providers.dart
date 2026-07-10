import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/session.dart';

/// Poll interval — overridable so the Sprint 9 WebSocket (or tests) can
/// replace the timer without touching consumers.
final nodePollIntervalProvider = Provider<Duration>(
  (ref) => const Duration(seconds: 10),
);

/// Node inventory, refreshed on an interval while watched. The timer is
/// cancelled on dispose (no leaked timers in tests or on tab switch).
final nodesProvider = StreamProvider.autoDispose<List<NodeInfo>>((ref) {
  final client = ref.watch(activeApiClientProvider);
  final controller = StreamController<List<NodeInfo>>();
  if (client == null) {
    controller.add(const []);
    ref.onDispose(controller.close);
    return controller.stream;
  }

  Future<void> tick() async {
    try {
      final nodes = await client.listNodes();
      if (!controller.isClosed) controller.add(nodes);
    } catch (error, stack) {
      if (!controller.isClosed) controller.addError(error, stack);
    }
  }

  tick();
  final timer = Timer.periodic(
    ref.watch(nodePollIntervalProvider),
    (_) => tick(),
  );
  ref.onDispose(() {
    timer.cancel();
    controller.close();
  });
  return controller.stream;
});

final nodeDetailProvider = FutureProvider.autoDispose.family<NodeInfo, String>((
  ref,
  id,
) async {
  final client = ref.watch(activeApiClientProvider);
  if (client == null) throw StateError('not authenticated');
  return client.getNode(id);
});
