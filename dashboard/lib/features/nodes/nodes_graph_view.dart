import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import '../../core/api_client.dart';
import '../../core/brand.dart';
import 'node_detail_screen.dart';

/// Fixed categorical hues (validated for CVD-safe adjacency), one per role —
/// same light/dark step pairs as the rest of the palette, assigned in a
/// stable order so a role's color never shifts as other roles come and go.
const _roleColorsLight = <String, Color>{
  'ai_compute': Color(0xFF2A78D6),
  'hybrid': Color(0xFF1BAF7A),
  'knowledge': Color(0xFFEDA100),
  'tool': Color(0xFF008300),
  'vision': Color(0xFF4A3AA7),
  'storage': Color(0xFFE34948),
};
const _roleColorsDark = <String, Color>{
  'ai_compute': Color(0xFF3987E5),
  'hybrid': Color(0xFF199E70),
  'knowledge': Color(0xFFC98500),
  'tool': Color(0xFF008300),
  'vision': Color(0xFF9085E9),
  'storage': Color(0xFFE66767),
};

/// Short glyph drawn inside each node so role identity never rests on color
/// alone (a colorblind or grayscale viewer still reads the role).
const _roleGlyphs = <String, String>{
  'ai_compute': 'AI',
  'hybrid': 'HY',
  'knowledge': 'KN',
  'tool': 'TL',
  'vision': 'VI',
  'storage': 'ST',
};

Color _roleColor(BuildContext context, String? role) {
  final map = Theme.of(context).brightness == Brightness.dark
      ? _roleColorsDark
      : _roleColorsLight;
  return (role != null ? map[role] : null) ??
      Theme.of(context).colorScheme.outlineVariant;
}

/// One body in the simulation: the controller hub, or a device node.
class _Body {
  _Body({required this.id, required this.position, this.node});

  final String id; // 'hub' or NodeInfo.id
  final NodeInfo? node; // null for the hub
  Offset position;
  Offset velocity = Offset.zero;
  bool pinned = false;
}

/// Star-topology force-directed graph of the fabric: the controller as a
/// central hub, every registered node as a spoke — this is the actual shape
/// of a Lycosa deployment (agents report only to the controller, never to
/// each other), rendered the way it is architecturally, not just visually.
class NodesGraphView extends StatefulWidget {
  const NodesGraphView({super.key, required this.nodes});

  final List<NodeInfo> nodes;

  @override
  State<NodesGraphView> createState() => _NodesGraphViewState();
}

class _NodesGraphViewState extends State<NodesGraphView>
    with SingleTickerProviderStateMixin {
  final Map<String, _Body> _bodies = {};
  late final Ticker _ticker;
  Duration _lastTick = Duration.zero;
  Size _canvasSize = Size.zero;
  String? _draggingId;

  static const _hubId = 'hub';
  static const _nodeRadius = 26.0;
  static const _hubRadius = 20.0;

  @override
  void initState() {
    super.initState();
    _ticker = createTicker(_onTick)..start();
    _seed();
  }

  @override
  void didUpdateWidget(covariant NodesGraphView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_sameNodeIds(oldWidget.nodes, widget.nodes)) _seed();
  }

  bool _sameNodeIds(List<NodeInfo> a, List<NodeInfo> b) {
    if (a.length != b.length) return false;
    final ids = a.map((n) => n.id).toSet();
    return b.every((n) => ids.contains(n.id));
  }

  void _seed() {
    final center = _canvasSize.isEmpty
        ? const Offset(300, 220)
        : _canvasSize.center(Offset.zero);
    final existing = Map<String, _Body>.from(_bodies);
    _bodies
      ..clear()
      ..[_hubId] = existing[_hubId] ?? _Body(id: _hubId, position: center);

    final count = widget.nodes.length;
    for (var i = 0; i < count; i++) {
      final n = widget.nodes[i];
      final angle = 2 * math.pi * i / math.max(count, 1);
      final start =
          existing[n.id]?.position ?? center + Offset.fromDirection(angle, 140);
      _bodies[n.id] = _Body(id: n.id, position: start, node: n)
        ..pinned = existing[n.id]?.pinned ?? false;
    }
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  void _onTick(Duration elapsed) {
    final dt = ((elapsed - _lastTick).inMicroseconds / 1e6).clamp(0.0, 0.05);
    _lastTick = elapsed;
    if (_canvasSize.isEmpty || _bodies.length < 2) return;

    final list = _bodies.values.toList();
    final forces = {for (final b in list) b.id: Offset.zero};

    // Repulsion between every pair — keeps nodes from overlapping.
    for (var i = 0; i < list.length; i++) {
      for (var j = i + 1; j < list.length; j++) {
        final a = list[i], b = list[j];
        var delta = a.position - b.position;
        var dist = delta.distance;
        if (dist < 1) {
          delta = Offset(
            math.Random(i * 31 + j).nextDouble() - 0.5,
            math.Random(j * 17 + i).nextDouble() - 0.5,
          );
          dist = delta.distance.clamp(0.01, double.infinity);
        }
        final push = delta / dist * (2600 / (dist * dist));
        forces[a.id] = forces[a.id]! + push;
        forces[b.id] = forces[b.id]! - push;
      }
    }

    // Spring: every device node pulled toward its rest distance from the hub.
    final hub = _bodies[_hubId]!;
    for (final b in list) {
      if (b.id == _hubId) continue;
      final delta = hub.position - b.position;
      final dist = delta.distance.clamp(0.01, double.infinity);
      const restLength = 150.0;
      final pull = delta / dist * (dist - restLength) * 0.9;
      forces[b.id] = forces[b.id]! + pull;
      forces[_hubId] = forces[_hubId]! - pull * 0.15;
    }

    // Gentle centering so the whole graph drifts back into view.
    final center = _canvasSize.center(Offset.zero);
    for (final b in list) {
      forces[b.id] = forces[b.id]! + (center - b.position) * 0.02;
    }

    const margin = 40.0;
    for (final b in list) {
      if (b.pinned) continue;
      b.velocity = (b.velocity + forces[b.id]! * dt) * 0.82;
      b.position += b.velocity * dt;
      b.position = Offset(
        b.position.dx.clamp(
          margin,
          math.max(margin, _canvasSize.width - margin),
        ),
        b.position.dy.clamp(
          margin,
          math.max(margin, _canvasSize.height - margin),
        ),
      );
    }
    setState(() {});
  }

  _Body? _hitTest(Offset point) {
    for (final b in _bodies.values) {
      final r = b.id == _hubId ? _hubRadius : _nodeRadius;
      if ((b.position - point).distance <= r + 6) return b;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    if (widget.nodes.isEmpty) {
      return const Center(
        child: Text(
          'No nodes yet. Add one and run lycosa-agent on the machine.',
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _Legend(nodes: widget.nodes),
        const SizedBox(height: 8),
        Expanded(
          child: LayoutBuilder(
            builder: (context, constraints) {
              _canvasSize = Size(constraints.maxWidth, constraints.maxHeight);
              return GestureDetector(
                onPanStart: (details) {
                  final hit = _hitTest(details.localPosition);
                  if (hit != null && hit.id != _hubId) {
                    _draggingId = hit.id;
                    hit.pinned = true;
                  }
                },
                onPanUpdate: (details) {
                  final id = _draggingId;
                  if (id == null) return;
                  setState(() => _bodies[id]!.position = details.localPosition);
                },
                onPanEnd: (_) {
                  final id = _draggingId;
                  if (id != null) _bodies[id]?.pinned = false;
                  _draggingId = null;
                },
                onTapUp: (details) {
                  final hit = _hitTest(details.localPosition);
                  if (hit != null && hit.node != null) {
                    Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (_) => NodeDetailScreen(nodeId: hit.node!.id),
                      ),
                    );
                  }
                },
                child: CustomPaint(
                  size: Size.infinite,
                  painter: _GraphPainter(
                    bodies: _bodies,
                    hubId: _hubId,
                    nodeRadius: _nodeRadius,
                    hubRadius: _hubRadius,
                    edgeColor: Theme.of(context).dividerColor,
                    hubColor: Theme.of(context).colorScheme.primary,
                    onHubColor: Theme.of(context).colorScheme.onPrimary,
                    textColor: Theme.of(context).colorScheme.onSurface,
                    mutedTextColor: Theme.of(
                      context,
                    ).colorScheme.onSurfaceVariant,
                    roleColor: (role) => _roleColor(context, role),
                  ),
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}

class _GraphPainter extends CustomPainter {
  _GraphPainter({
    required this.bodies,
    required this.hubId,
    required this.nodeRadius,
    required this.hubRadius,
    required this.edgeColor,
    required this.hubColor,
    required this.onHubColor,
    required this.textColor,
    required this.mutedTextColor,
    required this.roleColor,
  });

  final Map<String, _Body> bodies;
  final String hubId;
  final double nodeRadius;
  final double hubRadius;
  final Color edgeColor;
  final Color hubColor;
  final Color onHubColor;
  final Color textColor;
  final Color mutedTextColor;
  final Color Function(String? role) roleColor;

  @override
  void paint(Canvas canvas, Size size) {
    final hub = bodies[hubId];
    if (hub == null) return;

    final edgePaint = Paint()
      ..color = edgeColor
      ..strokeWidth = 2;
    for (final b in bodies.values) {
      if (b.id == hubId) continue;
      canvas.drawLine(hub.position, b.position, edgePaint);
    }

    // Hub: a diamond, distinct in shape (not just color) from device nodes.
    final hubPath = Path()
      ..moveTo(hub.position.dx, hub.position.dy - hubRadius)
      ..lineTo(hub.position.dx + hubRadius, hub.position.dy)
      ..lineTo(hub.position.dx, hub.position.dy + hubRadius)
      ..lineTo(hub.position.dx - hubRadius, hub.position.dy)
      ..close();
    canvas.drawPath(hubPath, Paint()..color = hubColor);
    _drawLabel(
      canvas,
      'Controller',
      hub.position + Offset(0, hubRadius + 14),
      mutedTextColor,
      bold: false,
    );
    _drawGlyph(canvas, hub.position, 'C', onHubColor);

    for (final b in bodies.values) {
      if (b.id == hubId) continue;
      final node = b.node!;
      final fill = roleColor(node.role ?? node.recommendedRole);
      canvas.drawCircle(b.position, nodeRadius, Paint()..color = fill);
      canvas.drawCircle(
        b.position,
        nodeRadius,
        Paint()
          ..color = statusColorFor(node.status)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3,
      );
      _drawGlyph(
        canvas,
        b.position,
        _roleGlyphs[node.role ?? node.recommendedRole] ?? '?',
        Colors.white,
      );
      _drawLabel(
        canvas,
        node.name,
        b.position + Offset(0, nodeRadius + 14),
        textColor,
        bold: true,
      );
    }
  }

  Color statusColorFor(String status) => LycosaColors.status(status);

  void _drawGlyph(Canvas canvas, Offset center, String text, Color color) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          color: color,
          fontSize: 11,
          fontWeight: FontWeight.bold,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(
      canvas,
      center - Offset(painter.width / 2, painter.height / 2),
    );
  }

  void _drawLabel(
    Canvas canvas,
    String text,
    Offset anchor,
    Color color, {
    required bool bold,
  }) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          color: color,
          fontSize: 12,
          fontWeight: bold ? FontWeight.w600 : FontWeight.normal,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: 120);
    painter.paint(canvas, anchor - Offset(painter.width / 2, 0));
  }

  @override
  bool shouldRepaint(covariant _GraphPainter oldDelegate) => true;
}

class _Legend extends StatelessWidget {
  const _Legend({required this.nodes});

  final List<NodeInfo> nodes;

  @override
  Widget build(BuildContext context) {
    final rolesInUse = nodes
        .map((n) => n.role ?? n.recommendedRole)
        .whereType<String>()
        .toSet();
    return Wrap(
      spacing: 16,
      runSpacing: 4,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        for (final role in nodeRoles.where(rolesInUse.contains))
          _swatch(context, _roleColor(context, role), role),
        const SizedBox(width: 8),
        _statusDot(context, LycosaColors.success, 'online'),
        _statusDot(context, LycosaColors.warning, 'registered'),
        _statusDot(context, LycosaColors.error, 'offline'),
      ],
    );
  }

  Widget _swatch(BuildContext context, Color color, String label) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Container(
        width: 12,
        height: 12,
        decoration: BoxDecoration(color: color, shape: BoxShape.circle),
      ),
      const SizedBox(width: 4),
      Text(label, style: Theme.of(context).textTheme.bodySmall),
    ],
  );

  Widget _statusDot(BuildContext context, Color color, String label) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Container(
        width: 10,
        height: 10,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          border: Border.all(color: color, width: 2),
        ),
      ),
      const SizedBox(width: 4),
      Text(label, style: Theme.of(context).textTheme.bodySmall),
    ],
  );
}
