const ROUND_LABELS: Record<string, string> = {
  'R1': 'Translate',
  'R1.5': 'Back-Translate',
  'R2': 'LSP Analysis',
  'R2.5': 'Graph Build',
  'R3': 'Classify',
  'R4': 'Verify',
  'R5': 'Report',
};

interface Props {
  rounds: string[];
  currentRound: string | null;
  completedRounds: Set<string>;
  isComplete: boolean;
  hasError: boolean;
}

export default function PipelineProgress({ rounds, currentRound, completedRounds, isComplete, hasError }: Props) {
  return (
    <div className="pipeline">
      {rounds.map((round, i) => {
        const isDone = completedRounds.has(round) || (isComplete && !hasError);
        const isActive = currentRound === round && !isComplete;
        const isErr = isActive && hasError;

        return (
          <div key={round} style={{ display: 'flex', alignItems: 'center' }}>
            {i > 0 && (
              <div className={`pipeline-connector ${isDone ? 'done' : ''}`} />
            )}
            <div className={`pipeline-step ${isDone ? 'done' : ''} ${isActive ? 'active' : ''} ${isErr ? 'error' : ''}`}>
              <span style={{ fontSize: 14 }}>
                {isDone ? '✓' : isActive ? '●' : isErr ? '✗' : '○'}
              </span>
              <span>{ROUND_LABELS[round] || round}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
