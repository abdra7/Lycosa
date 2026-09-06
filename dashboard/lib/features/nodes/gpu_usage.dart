import 'package:flutter/material.dart';

/// Uses the existing node heartbeat snapshot, with no additional polling.
class GpuUsage extends StatelessWidget {
  const GpuUsage({super.key, required this.metrics});
  final Map<String, dynamic> metrics;

  @override
  Widget build(BuildContext context) {
    final gpus = (metrics['gpus'] as List?) ?? const [];
    if (gpus.isEmpty) return const Text('GPU: Not Available');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final (index, gpu) in gpus.indexed) ...[
          const SizedBox(height: 8),
          Text('GPU ${gpu['index'] ?? index}: ${gpu['name'] ?? 'Unknown GPU'}'),
          _usage('GPU', gpu['utilization_percent'] ?? gpu['util_percent']),
          _usage('VRAM', gpu['memory_percent']),
          if (gpu['memory_used_mb'] is num && gpu['memory_total_mb'] is num)
            Text(
              '${((gpu['memory_used_mb'] as num) / 1024).toStringAsFixed(1)} / '
              '${((gpu['memory_total_mb'] as num) / 1024).toStringAsFixed(1)} GB VRAM',
            ),
          if ((gpu['temperature_c'] ?? gpu['temp_c']) is num)
            Text('GPU Temp: ${(gpu['temperature_c'] ?? gpu['temp_c'])}°C'),
        ],
      ],
    );
  }

  Widget _usage(String label, dynamic raw) {
    final percent = raw is num ? raw.toDouble() : null;
    if (percent == null || !percent.isFinite) {
      return Text('$label: Not Available');
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          SizedBox(width: 130, child: Text(label)),
          Expanded(
            child: TweenAnimationBuilder<double>(
              tween: Tween(end: percent.clamp(0, 100) / 100),
              duration: const Duration(milliseconds: 700),
              builder: (context, value, _) =>
                  LinearProgressIndicator(value: value, minHeight: 8),
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(width: 44, child: Text('${percent.toStringAsFixed(0)}%')),
        ],
      ),
    );
  }
}
