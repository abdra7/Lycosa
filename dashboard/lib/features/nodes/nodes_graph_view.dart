import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/brand.dart';
import 'node_detail_screen.dart';
import 'providers.dart';

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
  NodeInfo? node; // null for the hub; refreshed each poll for live metrics
  Offset position;
  Offset velocity = Offset.zero;
  bool pinned = false;
}

/// Star-topology force-directed graph of the fabric: the controller as a
/// central hub, every registered node as a spoke — this is the actual shape
/// of a Lycosa deployment (agents report only to the controller, never to
/// each other), rendered the way it is architecturally, not just visually.
class NodesGraphView extends StatefulWidget {
  const NodesGraphView({super.key, required this.nodes, this.fullscreen = false});

  final List<NodeInfo> nodes;

  /// True when hosted by [NodesGraphFullscreen] — flips the expand button
  /// into an exit button.
  final bool fullscreen;

  @override
  State<NodesGraphView> createState() => _NodesGraphViewState();
}

/// Full-window graph page. Watches [nodesProvider] itself so the graph keeps
/// receiving live status/metrics while expanded.
class NodesGraphFullscreen extends ConsumerWidget {
  const NodesGraphFullscreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final nodes = ref.watch(nodesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Fabric graph')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: nodes.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => Center(child: Text('Failed to load nodes: $e')),
          data: (list) => NodesGraphView(nodes: list, fullscreen: true),
        ),
      ),
    );
  }
}

class _NodesGraphViewState extends State<NodesGraphView>
    with SingleTickerProviderStateMixin {
  final Map<String, _Body> _bodies = {};
  late final Ticker _ticker;
  Duration _lastTick = Duration.zero;
  Size _canvasSize = Size.zero;
  String? _draggingId;

  // View transform: screen = world * zoom + pan.
  double _zoom = 1.0;
  Offset _panOffset = Offset.zero;
  bool _panningCanvas = false;

  static const _hubId = 'hub';
  static const _nodeRadius = 26.0;
  static const _hubRadius = 20.0;
  static const _minZoom = 0.4;
  static const _maxZoom = 3.0;

  Offset _toWorld(Offset local) => (local - _panOffset) / _zoom;

  void _zoomBy(double factor, {Offset? anchor}) {
    final old = _zoom;
    final next = (old * factor).clamp(_minZoom, _maxZoom).toDouble();
    if (next == old) return;
    final a = anchor ?? _canvasSize.center(Offset.zero);
    setState(() {
      // keep the anchor point (cursor or canvas center) fixed on screen
      _panOffset = a - (a - _panOffset) * (next / old);
      _zoom = next;
    });
  }

  void _resetView() => setState(() {
    _zoom = 1.0;
    _panOffset = Offset.zero;
  });

  @override
  void initState() {
    super.initState();
    _ticker = createTicker(_onTick)..start();
    _seed();
  }

  @override
  void didUpdateWidget(covariant NodesGraphView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_sameNodeIds(oldWidget.nodes, widget.nodes)) {
      _seed();
    } else {
      // same nodes, fresh poll: swap in the new data so status rings and
      // usage labels stay live without disturbing positions
      for (final n in widget.nodes) {
        _bodies[n.id]?.node = n;
      }
    }
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
              return Stack(
                children: [
                  Positioned.fill(
                    child: ClipRect(
                      child: Listener(
                        // mouse wheel / trackpad scroll = zoom at the cursor
                        onPointerSignal: (event) {
                          if (event is PointerScrollEvent) {
                            _zoomBy(
                              event.scrollDelta.dy < 0 ? 1.1 : 1 / 1.1,
                              anchor: event.localPosition,
                            );
                          }
                        },
                        child: GestureDetector(
                          onPanStart: (details) {
                            final hit = _hitTest(
                              _toWorld(details.localPosition),
                            );
                            if (hit != null && hit.id != _hubId) {
                              _draggingId = hit.id;
                              hit.pinned = true;
                            } else {
                              // empty space (or the hub): pan the whole canvas
                              _panningCanvas = true;
                            }
                          },
                          onPanUpdate: (details) {
                            final id = _draggingId;
                            if (id != null) {
                              setState(
                                () => _bodies[id]!.position = _toWorld(
                                  details.localPosition,
                                ),
                              );
                            } else if (_panningCanvas) {
                              setState(() => _panOffset += details.delta);
                            }
                          },
                          onPanEnd: (_) {
                            final id = _draggingId;
                            if (id != null) _bodies[id]?.pinned = false;
                            _draggingId = null;
                            _panningCanvas = false;
                          },
                          onTapUp: (details) {
                            final hit = _hitTest(
                              _toWorld(details.localPosition),
                            );
                            if (hit != null && hit.node != null) {
                              Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (_) =>
                                      NodeDetailScreen(nodeId: hit.node!.id),
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
                              zoom: _zoom,
                              panOffset: _panOffset,
                              edgeColor: Theme.of(context).dividerColor,
                              hubColor: Theme.of(context).colorScheme.primary,
                              onHubColor: Theme.of(
                                context,
                              ).colorScheme.onPrimary,
                              textColor: Theme.of(context).colorScheme.onSurface,
                              mutedTextColor: Theme.of(
                                context,
                              ).colorScheme.onSurfaceVariant,
                              roleColor: (role) => _roleColor(context, role),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  Positioned(right: 8, bottom: 8, child: _viewControls(context)),
                ],
              );
            },
          ),
        ),
      ],
    );
  }

  Widget _viewControls(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          IconButton(
            tooltip: 'Zoom in',
            icon: const Icon(Icons.add),
            onPressed: () => _zoomBy(1.2),
          ),
          IconButton(
            tooltip: 'Zoom out',
            icon: const Icon(Icons.remove),
            onPressed: () => _zoomBy(1 / 1.2),
          ),
          IconButton(
            tooltip: 'Reset view',
            icon: const Icon(Icons.center_focus_strong),
            onPressed: _resetView,
          ),
          if (widget.fullscreen)
            IconButton(
              tooltip: 'Exit full screen',
              icon: const Icon(Icons.fullscreen_exit),
              onPressed: () => Navigator.of(context).pop(),
            )
          else
            IconButton(
              tooltip: 'Expand',
              icon: const Icon(Icons.fullscreen),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const NodesGraphFullscreen()),
              ),
            ),
        ],
      ),
    );
  }
}

class _GraphPainter extends CustomPainter {
  _GraphPainter({
    required this.bodies,
    required this.hubId,
    required this.nodeRadius,
    required this.hubRadius,
    required this.zoom,
    required this.panOffset,
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
  final double zoom;
  final Offset panOffset;
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

    // View transform: everything below draws in world coordinates.
    canvas.save();
    canvas.translate(panOffset.dx, panOffset.dy);
    canvas.scale(zoom);

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
      // Live usage straight from the latest heartbeat — refreshed with every
      // node poll, so the graph doubles as a fabric-wide health monitor.
      final usage = _usageLine(node);
      if (usage != null) {
        _drawLabel(
          canvas,
          usage,
          b.position + Offset(0, nodeRadius + 32),
          mutedTextColor,
          bold: false,
          fontSize: 10.5,
        );
      }
    }

    canvas.restore();
  }

  String? _usageLine(NodeInfo node) {
    final metrics = node.metrics;
    if (metrics == null || node.status != 'online') return null;
    final cpu = (metrics['cpu_percent'] as num?)?.toStringAsFixed(0);
    final ram = (metrics['ram_percent'] as num?)?.toStringAsFixed(0);
    if (cpu == null && ram == null) return null;
    return 'CPU ${cpu ?? '—'}% · RAM ${ram ?? '—'}%';
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
    double fontSize = 12,
  }) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          color: color,
          fontSize: fontSize,
          fontWeight: bold ? FontWeight.w600 : FontWeight.normal,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: 140);
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
