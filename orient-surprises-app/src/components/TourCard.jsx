import Link from "next/link";
import styles from "./TourCard.module.css";

export default function TourCard({ tour }) {
  return (
    <div className={styles.tourCard}>
      <div className={styles.imgWrap}>
        <img src={tour.image} alt={tour.title} loading="lazy" />
        <span className={styles.duration}>
          {tour.durationDays} Days / {tour.durationNights} Nights
        </span>
        {tour.badge && <span className={styles.badge}>{tour.badge}</span>}
      </div>
      <div className={styles.body}>
        <div className={styles.cities}>{tour.cities.join(" · ")}</div>
        <h3 className={styles.title}>{tour.title}</h3>
        <p className={styles.best}>Best for: {tour.bestFor}</p>
        <div className={styles.price}>Price upon request</div>
        {tour.pdfUrl && (
          <a href={tour.pdfUrl} target="_blank" rel="noopener noreferrer" className={styles.pdf}>
            📄 View Full Itinerary PDF
          </a>
        )}
      </div>
    </div>
  );
}
