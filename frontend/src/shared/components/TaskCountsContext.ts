import { createContext, useContext } from 'react';

export const TaskCountsContext = createContext<{
  ticketCount: number | null;
  learningCount: number | null;
  refresh: () => void;
}>({ ticketCount: null, learningCount: null, refresh: () => {} });

export const useTaskCounts = () => useContext(TaskCountsContext);
