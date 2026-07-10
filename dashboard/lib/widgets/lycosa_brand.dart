import 'package:flutter/material.dart';

/// The Lycosa lockup: spider mark on the left, wordmark on the right.
/// Used in the app header (top-left), and standalone on auth screens.
class LycosaBrand extends StatelessWidget {
  const LycosaBrand({super.key, this.logoSize = 34, this.showWordmark = true});

  final double logoSize;
  final bool showWordmark;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Image.asset(
          'assets/brand/lycosa_logo.png',
          width: logoSize,
          height: logoSize,
          filterQuality: FilterQuality.medium,
        ),
        if (showWordmark) ...[
          const SizedBox(width: 10),
          Text(
            'Lycosa',
            style: TextStyle(
              fontSize: logoSize * 0.56,
              fontWeight: FontWeight.w700,
              letterSpacing: -0.4,
              color: Theme.of(context).colorScheme.onSurface,
            ),
          ),
        ],
      ],
    );
  }
}

/// Centered brand header for the connection and login screens.
class LycosaBrandHero extends StatelessWidget {
  const LycosaBrandHero({super.key, this.subtitle});

  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Image.asset(
          'assets/brand/lycosa_logo.png',
          width: 56,
          height: 56,
          filterQuality: FilterQuality.medium,
        ),
        const SizedBox(height: 12),
        Text(
          'Lycosa',
          style: TextStyle(
            fontSize: 24,
            fontWeight: FontWeight.w700,
            letterSpacing: -0.5,
            color: Theme.of(context).colorScheme.onSurface,
          ),
        ),
        if (subtitle != null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              subtitle!,
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 13,
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ),
      ],
    );
  }
}
