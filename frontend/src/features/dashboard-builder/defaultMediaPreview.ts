import {
  dashboardAssetMediaUrl,
  galleryContentUrlById,
  workflowDefaultAssetMediaUrl,
} from "../../lib/api/noofyApi";
import type { DashboardWidget, WidgetType } from "./dashboardBuilderContent";
import {
  isGalleryMediaReference,
  isPackageAssetReference,
  isUploadedAssetValue,
} from "../workflows/media";

type BuilderMediaKind = "image" | "audio" | "video" | "3d" | "file";

export interface BuilderDefaultMediaPreview {
  kind: BuilderMediaKind;
  label: string;
  url: string | null;
}

export function builderDefaultMediaPreview(
  workflowId: string,
  widget: DashboardWidget,
): BuilderDefaultMediaPreview | null {
  const kind = mediaKindForWidgetType(widget.widgetType);
  if (!kind) return null;
  const value = widget.defaultValue;

  if (isUploadedAssetValue(value)) {
    return {
      kind,
      label: value,
      url: dashboardAssetMediaUrl(value),
    };
  }

  if (isPackageAssetReference(value) && value.kind === kind) {
    return {
      kind,
      label: value.filename ?? value.asset_id,
      url: workflowId
        ? workflowDefaultAssetMediaUrl(
            workflowId,
            widget.backendInputId ?? widget.valueId ?? widget.id,
            value.asset_id,
          )
        : null,
    };
  }

  if (isGalleryMediaReference(value) && value.kind === kind) {
    return {
      kind,
      label: value.filename,
      url: galleryContentUrlById(value.gallery_item_id),
    };
  }

  return null;
}

export function hasBuilderDefaultMedia(widget: DashboardWidget): boolean {
  const kind = mediaKindForWidgetType(widget.widgetType);
  const value = widget.defaultValue;
  if (!kind) return false;
  if (isUploadedAssetValue(value)) return true;
  if (isPackageAssetReference(value)) return value.kind === kind;
  if (isGalleryMediaReference(value)) return value.kind === kind;
  return false;
}

function mediaKindForWidgetType(widgetType: WidgetType): BuilderMediaKind | null {
  if (widgetType === "load_image" || widgetType === "load_image_mask") return "image";
  if (widgetType === "load_audio") return "audio";
  if (widgetType === "load_video") return "video";
  if (widgetType === "load_3d") return "3d";
  if (widgetType === "load_file") return "file";
  return null;
}
