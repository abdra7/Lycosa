//! Errors returned to the frontend. Each variant is a tagged JSON object
//! (`{"kind": "api", ...}`), so the TypeScript client can tell a controller
//! error envelope (ADR-007) from an unreachable controller.

use serde::Serialize;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct FieldError {
    pub field: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, thiserror::Error, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AppError {
    /// The controller answered with an error envelope.
    #[error("{message}")]
    Api {
        status: u16,
        code: String,
        message: String,
        details: Vec<FieldError>,
    },
    /// DNS, connection refused, TLS or timeout: no answer from the controller.
    #[error("could not reach controller at {base_url}: {message}")]
    Unreachable { base_url: String, message: String },
    /// No profile is active, or the active one has no session.
    #[error("not signed in")]
    NotSignedIn,
    /// A command argument failed validation.
    #[error("{message}")]
    Invalid { message: String },
    /// A credential would leave over plain HTTP to a non-loopback host, or
    /// the controller tried to redirect a credential-bearing request.
    #[error("{message}")]
    Insecure { message: String },
    /// The OS keychain refused an operation.
    #[error("{message}")]
    Keychain { message: String },
    /// The standalone services could not be managed.
    #[error("{message}")]
    Supervisor { message: String },
    /// Anything else (I/O on the app's own files, a broken stream).
    #[error("{message}")]
    Internal { message: String },
}

impl AppError {
    pub fn invalid(message: impl Into<String>) -> Self {
        Self::Invalid {
            message: message.into(),
        }
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::Internal {
            message: message.into(),
        }
    }

    pub fn supervisor(message: impl Into<String>) -> Self {
        Self::Supervisor {
            message: message.into(),
        }
    }

    /// Parse the controller's `{"error": {"code", "message", "details"}}`
    /// envelope; missing pieces fall back like the Dart client did.
    pub fn from_envelope(status: u16, body: &serde_json::Value) -> Self {
        let error = body.get("error");
        let text = |key: &str, fallback: &str| {
            error
                .and_then(|e| e.get(key))
                .and_then(|v| v.as_str())
                .unwrap_or(fallback)
                .to_string()
        };
        let details = error
            .and_then(|e| e.get("details"))
            .and_then(|d| d.as_array())
            .map(|items| {
                items
                    .iter()
                    .map(|d| FieldError {
                        field: d
                            .get("field")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        message: d
                            .get("message")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                    })
                    .collect()
            })
            .unwrap_or_default();
        Self::Api {
            status,
            code: text("code", "error"),
            message: text("message", "Request failed"),
            details,
        }
    }
}

impl From<std::io::Error> for AppError {
    fn from(error: std::io::Error) -> Self {
        Self::internal(error.to_string())
    }
}

pub type AppResult<T> = Result<T, AppError>;

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_the_controller_envelope() {
        let body = json!({"error": {"code": "validation_error", "message": "Invalid input",
            "details": [{"field": "email", "message": "required"}]}});
        assert_eq!(
            AppError::from_envelope(422, &body),
            AppError::Api {
                status: 422,
                code: "validation_error".into(),
                message: "Invalid input".into(),
                details: vec![FieldError {
                    field: "email".into(),
                    message: "required".into()
                }],
            }
        );
    }

    #[test]
    fn a_body_without_an_envelope_falls_back() {
        let error = AppError::from_envelope(502, &json!("upstream broke"));
        assert_eq!(
            error,
            AppError::Api {
                status: 502,
                code: "error".into(),
                message: "Request failed".into(),
                details: vec![],
            }
        );
    }

    #[test]
    fn serializes_with_a_kind_tag() {
        let value = serde_json::to_value(AppError::NotSignedIn).unwrap();
        assert_eq!(value, json!({"kind": "not_signed_in"}));
        let value = serde_json::to_value(AppError::invalid("bad")).unwrap();
        assert_eq!(value, json!({"kind": "invalid", "message": "bad"}));
    }
}
