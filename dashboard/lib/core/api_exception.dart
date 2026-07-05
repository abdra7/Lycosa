/// Error shape mirroring the controller's envelope (ADR-007):
/// {"error": {"code", "message", "details": [{"field","message"}]}}
class ApiException implements Exception {
  ApiException({
    required this.statusCode,
    required this.code,
    required this.message,
    this.details = const [],
  });

  final int statusCode;
  final String code;
  final String message;
  final List<Map<String, dynamic>> details;

  factory ApiException.fromEnvelope(int statusCode, Map<String, dynamic> body) {
    final error = body['error'] as Map<String, dynamic>? ?? {};
    return ApiException(
      statusCode: statusCode,
      code: error['code'] as String? ?? 'error',
      message: error['message'] as String? ?? 'Request failed',
      details: (error['details'] as List?)?.cast<Map<String, dynamic>>() ?? const [],
    );
  }

  bool get isUnauthorized => statusCode == 401;

  /// Human-friendly single line, including field errors when present.
  String get friendly {
    if (details.isEmpty) return message;
    final fields = details
        .map((d) => '${d['field']}: ${d['message']}')
        .join('; ');
    return '$message ($fields)';
  }

  @override
  String toString() => 'ApiException($statusCode $code): $message';
}

/// Controller unreachable / DNS / timeout — distinct from an API error.
class ControllerUnreachableException implements Exception {
  ControllerUnreachableException(this.baseUrl, this.cause);

  final String baseUrl;
  final Object cause;

  String get friendly => 'Could not reach controller at $baseUrl';

  @override
  String toString() => 'ControllerUnreachableException($baseUrl): $cause';
}
