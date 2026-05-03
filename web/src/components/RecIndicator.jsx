import './RecIndicator.css';

export default function RecIndicator({ pulsing = true }) {
  return (
    <div className="rec-indicator">
      <div className="rec-indicator__dot-wrapper">
        <div
          className={
            'rec-indicator__halo' +
            (pulsing ? ' rec-indicator__halo--pulsing' : '')
          }
        />
        <div className="rec-indicator__dot" />
      </div>
      <span className="rec-indicator__label">REC</span>
    </div>
  );
}
