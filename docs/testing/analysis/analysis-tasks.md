# Tasks Module - Task Analysis

## 1. Business Overview
Core ticket management system. Engineers create, process, and resolve fault tickets through defined state machine.

## 2. API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| POST | /api/tasks | Create ticket |
| GET | /api/tasks | List tickets (with pagination) |
| GET | /api/tasks/{id} | Get ticket detail |
| PUT | /api/tasks/{id} | Update ticket |
| PATCH | /api/tasks/{id}/status | Transition status |
| PATCH | /api/tasks/{id}/assign | Assign engineer |
| DELETE | /api/tasks/{id} | Delete ticket |
| POST | /api/tasks/filter | Filter tickets |
| GET | /api/tasks/stats/overview | Get statistics |
| POST | /api/tasks/{id}/comments | Add comment |
| GET | /api/tasks/{id}/comments | List comments |
| POST | /api/tasks/{id}/ai-assign | AI auto-assign |

## 3. Status Machine
pending -> in_progress -> resolved -> closed
pending -> cancelled
in_progress -> cancelled

## 4. Key Business Rules
- Title and description are required for creation
- Status transitions must follow state machine
- Only assigner or admin can change assignment
- Comments are immutable after creation
- Deleted tickets are soft-deleted

## 5. Risk Areas
- Invalid status transitions returning 400
- Concurrent status updates
- Large comment volumes on single ticket
- AI-assign confidence scoring
