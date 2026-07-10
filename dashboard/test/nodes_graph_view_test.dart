import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lycosa_dashboard/core/api_client.dart';
import 'package:lycosa_dashboard/features/nodes/nodes_graph_view.dart';

import 'api_client_nodes_test.dart' show nodeJson;

// Node/hub labels are painted directly on a Canvas (TextPainter), not as
// Text widgets, so find.text() can't see them — these tests check what's
// actually inspectable: the legend (real widgets) and painter stability.
void main() {
  testWidgets('graph view builds a legend entry per role in use and paints '
      'without throwing', (tester) async {
    final nodes = [
      NodeInfo.fromJson(nodeJson(role: 'hybrid')),
      NodeInfo.fromJson({
        ...nodeJson(role: null),
        'id': 'n2',
        'name': 'second-box',
        'status': 'offline',
      }),
    ];

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: NodesGraphView(nodes: nodes)),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 16));

    expect(find.text('hybrid'), findsOneWidget); // legend entry
    expect(find.text('online'), findsOneWidget); // status legend
    expect(find.text('offline'), findsOneWidget);
    expect(find.byType(CustomPaint), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets('empty node list shows the same empty state as the list view', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: NodesGraphView(nodes: [])),
      ),
    );
    await tester.pump();

    expect(find.textContaining('No nodes yet'), findsOneWidget);
  });

  testWidgets('graph keeps animating (ticker) across several frames without '
      'throwing', (tester) async {
    final nodes = [
      NodeInfo.fromJson(nodeJson(role: 'hybrid')),
      NodeInfo.fromJson({
        ...nodeJson(role: 'storage'),
        'id': 'n2',
        'name': 'b',
      }),
      NodeInfo.fromJson({...nodeJson(role: null), 'id': 'n3', 'name': 'c'}),
    ];
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: NodesGraphView(nodes: nodes)),
      ),
    );
    for (var i = 0; i < 30; i++) {
      await tester.pump(const Duration(milliseconds: 16));
    }

    expect(tester.takeException(), isNull);
  });

  testWidgets('graph rebuilds cleanly when the node list changes', (
    tester,
  ) async {
    var nodes = [NodeInfo.fromJson(nodeJson(role: 'hybrid'))];
    late StateSetter setState;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, set) {
              setState = set;
              return NodesGraphView(nodes: nodes);
            },
          ),
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 16));

    setState(() {
      nodes = [
        ...nodes,
        NodeInfo.fromJson({
          ...nodeJson(role: 'storage'),
          'id': 'n2',
          'name': 'b',
        }),
      ];
    });
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 16));

    expect(find.text('storage'), findsOneWidget); // new legend entry
    expect(tester.takeException(), isNull);
  });
}
