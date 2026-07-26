# Call Module - Task Analysis

## 1. Business Overview
Users submit fault reports and get AI-powered troubleshooting assistance through WeChat H5.
Core flow: Submit issue -> AI QA conversation -> Convert to ticket -> Track resolution.

## 2. API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| POST | /api/conversations | Create conversation |
| GET | /api/conversations | List conversations |
| GET | /api/conversations/{id} | Get conversation detail |
| PUT | /api/conversations/{id} | Update conversation |
| DELETE | /api/conversations/{id} | Delete conversation |
| POST | /api/qa/ask | Send QA question |
| POST | /api/qa/ask/stream | Streaming QA (SSE) |
| POST | /api/messages | Send message |
| GET | /api/messages | List messages |
| GET | /api/my-tasks/ | List my tasks |
| POST | /api/my-tasks/ | Create my task |

## 3. Business Flow
User WX -> POST /auth/login -> POST /conversations -> POST /qa/ask -> GET /conversations/{id} -> ...

## 4. Key Business Rules
- QA responses must be relevant to the question
- Conversations have a 1:N relationship with messages
- My-tasks shows user-scoped ticket list
- SSE streaming supports real-time response display

## 5. Risk Areas
- SSE streaming connection handling
- Conversation ownership validation
- QA response with empty context
