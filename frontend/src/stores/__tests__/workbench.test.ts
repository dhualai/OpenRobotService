import { describe, it, expect, beforeEach } from 'vitest';
import { useWorkbenchStore } from '../workbench';

describe('Workbench Store', () => {
  beforeEach(() => {
    useWorkbenchStore.setState({
      activeTab: 'call',
      ticketDraft: null,
      chatContext: null,
      vehicleContext: null,
      tasksRefreshKey: 0,
      selectedTicketId: null,
    });
  });

  describe('initial state', () => {
    it('should have call as default activeTab', () => {
      const state = useWorkbenchStore.getState();
      expect(state.activeTab).toBe('call');
    });

    it('should have null ticketDraft, chatContext and vehicleContext', () => {
      const state = useWorkbenchStore.getState();
      expect(state.ticketDraft).toBeNull();
      expect(state.chatContext).toBeNull();
      expect(state.vehicleContext).toBeNull();
    });

    it('should have tasksRefreshKey at 0', () => {
      expect(useWorkbenchStore.getState().tasksRefreshKey).toBe(0);
    });
  });

  describe('setActiveTab', () => {
    it('should switch active tab', () => {
      useWorkbenchStore.getState().setActiveTab('tasks');
      expect(useWorkbenchStore.getState().activeTab).toBe('tasks');

      useWorkbenchStore.getState().setActiveTab('admin');
      expect(useWorkbenchStore.getState().activeTab).toBe('admin');
    });
  });

  describe('goToTab', () => {
    it('should switch tab and set payload data', () => {
      useWorkbenchStore.getState().goToTab('tasks', {
        ticketDraft: { title: 'Test', description: 'Desc' },
        selectedTicketId: 'ticket-1',
      });

      const state = useWorkbenchStore.getState();
      expect(state.activeTab).toBe('tasks');
      expect(state.ticketDraft).toEqual({ title: 'Test', description: 'Desc' });
      expect(state.selectedTicketId).toBe('ticket-1');
    });

    it('should set chatContext when provided', () => {
      useWorkbenchStore.getState().goToTab('call', {
        chatContext: { ticketId: 't-1', title: '讨论工单' },
      });

      expect(useWorkbenchStore.getState().chatContext).toEqual({
        ticketId: 't-1',
        title: '讨论工单',
      });
    });

    it('should not overwrite fields when not in payload', () => {
      useWorkbenchStore.getState().setTicketDraft({ title: 'Existing', description: 'Old' });
      useWorkbenchStore.getState().goToTab('admin', {});

      const state = useWorkbenchStore.getState();
      expect(state.activeTab).toBe('admin');
      // ticketDraft should remain unchanged when not in payload
      expect(state.ticketDraft).toEqual({ title: 'Existing', description: 'Old' });
    });
  });

  describe('consumeTicketDraft', () => {
    it('should return draft and clear it', () => {
      const draft = { title: 'New Ticket', description: 'Bug report' };
      useWorkbenchStore.getState().setTicketDraft(draft);

      const consumed = useWorkbenchStore.getState().consumeTicketDraft();
      expect(consumed).toEqual(draft);
      expect(useWorkbenchStore.getState().ticketDraft).toBeNull();
    });

    it('should return null when no draft', () => {
      const consumed = useWorkbenchStore.getState().consumeTicketDraft();
      expect(consumed).toBeNull();
    });
  });

  describe('consumeChatContext', () => {
    it('should return context and clear it', () => {
      const ctx = { ticketId: 't-1', title: 'Discussion' };
      useWorkbenchStore.getState().setChatContext(ctx);

      const consumed = useWorkbenchStore.getState().consumeChatContext();
      expect(consumed).toEqual(ctx);
      expect(useWorkbenchStore.getState().chatContext).toBeNull();
    });

    it('should return null when no context', () => {
      const consumed = useWorkbenchStore.getState().consumeChatContext();
      expect(consumed).toBeNull();
    });
  });

  describe('vehicleContext（扫码确认后带入对话）', () => {
    const VEHICLE = {
      scene: 'proj_abc123def456',
      projectName: '项目A',
      customerName: '客户A',
      vehicleModel: 'XQE',
    };

    it('should set and read back vehicle context', () => {
      useWorkbenchStore.getState().setVehicleContext(VEHICLE);
      expect(useWorkbenchStore.getState().vehicleContext).toEqual(VEHICLE);
    });

    it('should return context and clear it（消费即置空，避免重复注入）', () => {
      useWorkbenchStore.getState().setVehicleContext(VEHICLE);

      const consumed = useWorkbenchStore.getState().consumeVehicleContext();
      expect(consumed).toEqual(VEHICLE);
      expect(useWorkbenchStore.getState().vehicleContext).toBeNull();
    });

    it('should return null when no context', () => {
      expect(useWorkbenchStore.getState().consumeVehicleContext()).toBeNull();
    });

    it('should clear on explicit null（用户点「暂不」时不该留残留）', () => {
      useWorkbenchStore.getState().setVehicleContext(VEHICLE);
      useWorkbenchStore.getState().setVehicleContext(null);
      expect(useWorkbenchStore.getState().vehicleContext).toBeNull();
    });
  });

  describe('refreshTasks', () => {
    it('should increment tasksRefreshKey', () => {
      expect(useWorkbenchStore.getState().tasksRefreshKey).toBe(0);
      useWorkbenchStore.getState().refreshTasks();
      expect(useWorkbenchStore.getState().tasksRefreshKey).toBe(1);
      useWorkbenchStore.getState().refreshTasks();
      expect(useWorkbenchStore.getState().tasksRefreshKey).toBe(2);
    });
  });

  describe('setSelectedTicketId', () => {
    it('should update selectedTicketId', () => {
      useWorkbenchStore.getState().setSelectedTicketId('ticket-42');
      expect(useWorkbenchStore.getState().selectedTicketId).toBe('ticket-42');

      useWorkbenchStore.getState().setSelectedTicketId(null);
      expect(useWorkbenchStore.getState().selectedTicketId).toBeNull();
    });
  });
});
