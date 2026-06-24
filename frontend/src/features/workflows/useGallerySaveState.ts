import { useEffect, useMemo, useState } from "react";

import {
  cancelJobOutputGallerySave,
  fetchJobGalleryStatus,
  saveJobOutputToGallery,
  type GallerySaveRequest,
  type JobResult,
} from "../../lib/api/noofyApi";
import { failedGallerySaveRequest } from "./workflowRunOutputs";

export function useGallerySaveState(result: JobResult | null) {
  const [gallerySaveByControlId, setGallerySaveByControlId] = useState<Record<string, GallerySaveRequest>>({});
  const hasActiveGallerySave = useMemo(
    () => Object.values(gallerySaveByControlId).some((item) => item.status === "queued" || item.status === "saving"),
    [gallerySaveByControlId],
  );

  useEffect(() => {
    const jobId = result?.status === "completed" ? result.job_id : null;
    if (!jobId) {
      setGallerySaveByControlId({});
      return;
    }
    let stopped = false;
    const refresh = async () => {
      try {
        const response = await fetchJobGalleryStatus(jobId);
        if (stopped) return;
        setGallerySaveByControlId(Object.fromEntries(response.outputs.map((item) => [item.control_id, item])));
      } catch {
        // Saving remains optional to the completed workflow result.
      }
    };
    void refresh();
    return () => {
      stopped = true;
    };
  }, [result?.job_id, result?.status]);

  useEffect(() => {
    const jobId = result?.status === "completed" ? result.job_id : null;
    if (!jobId || !hasActiveGallerySave) return undefined;
    const interval = window.setInterval(() => {
      fetchJobGalleryStatus(jobId)
        .then((response) => setGallerySaveByControlId(Object.fromEntries(response.outputs.map((item) => [item.control_id, item]))))
        .catch(() => undefined);
    }, 700);
    return () => window.clearInterval(interval);
  }, [hasActiveGallerySave, result?.job_id, result?.status]);

  async function saveOutputToGallery(controlId: string) {
    if (result?.status !== "completed") return;
    try {
      const request = await saveJobOutputToGallery(result.job_id, controlId);
      setGallerySaveByControlId((current) => ({ ...current, [controlId]: request }));
    } catch (error) {
      setGallerySaveByControlId((current) => ({
        ...current,
        [controlId]: failedGallerySaveRequest(result.job_id, controlId, error),
      }));
    }
  }

  async function cancelOutputGallerySave(controlId: string) {
    if (result?.status !== "completed") return;
    try {
      const request = await cancelJobOutputGallerySave(result.job_id, controlId);
      setGallerySaveByControlId((current) => ({ ...current, [controlId]: request }));
    } catch {
      // Keep polling: the background save may still complete or accept a later cancel.
    }
  }

  return {
    gallerySaveByControlId,
    saveOutputToGallery,
    cancelOutputGallerySave,
  };
}
