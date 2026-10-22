// Server state and its polling cadence: the node list and
// recent tasks every 10 s, one node every 5 s (its agent heartbeats every
// 5 s), a workflow run every 3 s until it finishes. Every key is dropped
// when the active profile changes (SessionProvider clears the cache).

import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import { runFinished } from "./types";

export const NODE_POLL_MS = 10_000;
export const NODE_DETAIL_POLL_MS = 5_000;
export const RUN_POLL_MS = 3_000;

export const useNodes = () => useQuery({ queryKey: ["nodes"], queryFn: api.listNodes, refetchInterval: NODE_POLL_MS });

export const useNode = (id: string) =>
  useQuery({ queryKey: ["nodes", id], queryFn: () => api.getNode(id), refetchInterval: NODE_DETAIL_POLL_MS });

export const useRecommendations = (id: string) =>
  useQuery({ queryKey: ["nodes", id, "llm-recommendations"], queryFn: () => api.getLlmRecommendations(id) });

export const useTasks = () => useQuery({ queryKey: ["tasks"], queryFn: api.listTasks, refetchInterval: NODE_POLL_MS });

export const useWorkflows = () => useQuery({ queryKey: ["workflows"], queryFn: api.listWorkflows });

export const useRun = (workflowId: string, runId: string) =>
  useQuery({
    queryKey: ["runs", workflowId, runId],
    queryFn: () => api.getRun(workflowId, runId),
    refetchInterval: (query) => (query.state.data && runFinished(query.state.data) ? false : RUN_POLL_MS),
  });

export const useCollections = () => useQuery({ queryKey: ["collections"], queryFn: api.listCollections });

export const useDocuments = (collectionId: string) =>
  useQuery({ queryKey: ["collections", collectionId, "documents"], queryFn: () => api.listDocuments(collectionId) });

export const useAuditLogs = () => useQuery({ queryKey: ["audit-logs"], queryFn: () => api.listAuditLogs(100) });

export const useApiKeys = () => useQuery({ queryKey: ["api-keys"], queryFn: api.listApiKeys });

export const useLlmProviders = () => useQuery({ queryKey: ["llm", "providers"], queryFn: api.listLlmProviders });

export const useLlmAccounts = () => useQuery({ queryKey: ["llm", "accounts"], queryFn: api.listLlmAccounts });

export const useLlmRouting = () => useQuery({ queryKey: ["llm", "routing"], queryFn: api.getLlmRouting });

export const useAccountModels = (accountId: string | null) =>
  useQuery({
    queryKey: ["llm", "accounts", accountId, "models"],
    queryFn: () => api.listLlmAccountModels(accountId!),
    enabled: !!accountId,
  });
