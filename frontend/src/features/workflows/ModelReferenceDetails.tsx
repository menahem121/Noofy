import type { RequiredModelReference } from "../../lib/api/noofyApi";

/**
 * Collapsible developer detail listing every workflow node that loads a model file.
 *
 * The model summary shows one card per physical file, but a single file can be loaded
 * by several graph nodes. This surfaces those node references for debugging without
 * cluttering the beginner-facing card. It renders nothing when only one node uses the
 * file (the common case).
 */
export function ModelReferenceDetails({
  references,
  dedupUncertain = false,
}: {
  references?: RequiredModelReference[];
  dedupUncertain?: boolean;
}) {
  if (!references || references.length <= 1) return null;
  return (
    <details className="required-model-row__references">
      <summary>Show technical details</summary>
      {dedupUncertain ? (
        <p>These references were grouped because they use the same model folder and filename.</p>
      ) : null}
      <p>Workflow nodes ({references.length})</p>
      <ul>
        {references.map((reference, index) => (
          <li key={`${reference.requirement_id}:${index}`}>
            {[reference.node_type ?? "Node", reference.node_id, reference.input_name]
              .filter(Boolean)
              .join(" · ")}
          </li>
        ))}
      </ul>
    </details>
  );
}
