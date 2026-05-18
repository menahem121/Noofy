interface ModelVerificationProgressJob {
  current_model_filename: string | null;
  current_model_index: number | null;
  total_models: number;
  percent: number | null;
  user_facing_message: string;
}

export function ModelVerificationProgressPanel({
  job,
  idleLabel = "Verifying local model files...",
  idleMessage = "Verifying local model files...",
}: {
  job: ModelVerificationProgressJob | null;
  idleLabel?: string;
  idleMessage?: string;
}) {
  const percent = job?.percent !== null && job?.percent !== undefined
    ? Math.max(0, Math.min(Number(job.percent), 100))
    : null;
  const label = job?.current_model_filename
    ? `Model ${job.current_model_index ?? 1} of ${job.total_models}: ${job.current_model_filename}`
    : idleLabel;
  const percentLabel = percent !== null
    ? `${Number.isInteger(percent) ? percent : percent.toFixed(1)}%`
    : "Checking";

  return (
    <div className="model-download-progress" role="status">
      <div className="model-download-progress__header">
        <strong>{label}</strong>
        <span>{percentLabel}</span>
      </div>
      <div
        className="model-download-progress__bar"
        role="progressbar"
        aria-label="Model verification progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? 0}
      >
        <div
          className="model-download-progress__bar-fill"
          style={{ width: `${percent ?? 0}%` }}
        />
      </div>
      <span>{job?.user_facing_message ?? idleMessage}</span>
    </div>
  );
}
