import { useEffect, useRef, useState } from "react";

import {
  fetchAssetBlobUrl,
  galleryContentUrlById,
  workflowDefaultAssetMediaUrl,
  type DashboardControlDef,
  type WorkflowPackageResponse,
} from "../../lib/api/noofyApi";
import {
  comparisonImageSourceForRun,
} from "./workflowRunOutputs";
import type { ComparisonImageSource } from "./workflowRunStateTypes";

export function useRunComparisonInputImage(workflowId: string) {
  const [runComparisonInputSource, setRunComparisonInputSource] = useState<ComparisonImageSource | null>(null);
  const [comparisonInputImageUrl, setComparisonInputImageUrl] = useState<string | null>(null);
  const comparisonSourceResolutionSequenceRef = useRef(0);
  const activeWorkflowIdRef = useRef(workflowId);
  activeWorkflowIdRef.current = workflowId;

  useEffect(() => {
    setComparisonInputImageUrl(null);
    if (!runComparisonInputSource) return undefined;

    if (runComparisonInputSource.kind === "package_asset") {
      setComparisonInputImageUrl(
        workflowDefaultAssetMediaUrl(
          runComparisonInputSource.workflowId,
          runComparisonInputSource.inputId,
          runComparisonInputSource.assetId,
        ),
      );
      return undefined;
    }

    if (runComparisonInputSource.kind === "gallery_reference") {
      setComparisonInputImageUrl(galleryContentUrlById(runComparisonInputSource.galleryItemId));
      return undefined;
    }

    let canceled = false;
    let objectUrl: string | null = null;
    const assetId =
      runComparisonInputSource.kind === "masked_source_asset"
        ? runComparisonInputSource.sourceAssetId
        : runComparisonInputSource.assetId;
    fetchAssetBlobUrl(assetId)
      .then((url) => {
        if (canceled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setComparisonInputImageUrl(url);
      })
      .catch(() => {
        if (!canceled) setComparisonInputImageUrl(null);
      });

    return () => {
      canceled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [runComparisonInputSource]);

  function clearRunComparisonInputSource() {
    comparisonSourceResolutionSequenceRef.current += 1;
    setRunComparisonInputSource(null);
  }

  function resolveRunComparisonInputSource(
    packageData: WorkflowPackageResponse | null,
    controls: DashboardControlDef[],
    inputValues: Record<string, unknown>,
  ) {
    const sourceWorkflowId = workflowId;
    const sequence = ++comparisonSourceResolutionSequenceRef.current;
    setRunComparisonInputSource(null);
    void comparisonImageSourceForRun(sourceWorkflowId, packageData, controls, inputValues)
      .then((source) => {
        if (
          sequence !== comparisonSourceResolutionSequenceRef.current ||
          activeWorkflowIdRef.current !== sourceWorkflowId
        ) {
          return;
        }
        setRunComparisonInputSource(source);
      })
      .catch(() => {
        // Comparison is optional. Failed metadata/source resolution should not
        // affect run submission or the normal output preview path.
      });
  }

  return {
    comparisonInputImageUrl,
    clearRunComparisonInputSource,
    resolveRunComparisonInputSource,
  };
}
