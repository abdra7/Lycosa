import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lycosa_dashboard/features/nodes/gpu_usage.dart';

void main() {
  testWidgets('unsupported GPU is unavailable, never a zero progress bar', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: GpuUsage(metrics: {})),
      ),
    );
    expect(find.text('GPU: Not Available'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsNothing);
  });

  testWidgets('renders separate GPUs, memory totals and optional temperature', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: GpuUsage(
            metrics: {
              'gpus': [
                {
                  'index': 0,
                  'name': 'GPU A',
                  'utilization_percent': 62,
                  'memory_used_mb': 6144,
                  'memory_total_mb': 12288,
                  'memory_percent': 50,
                  'temperature_c': 63,
                },
                {'index': 1, 'name': 'GPU B'},
              ],
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('GPU 0: GPU A'), findsOneWidget);
    expect(find.text('GPU 1: GPU B'), findsOneWidget);
    expect(find.text('6.0 / 12.0 GB VRAM'), findsOneWidget);
    expect(find.text('GPU: Not Available'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsNWidgets(2));
  });
}
