import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import styles from "./page.module.css";

export default function AboutPage() {
  return (
    <>
      <Navbar />

      <header className={styles.pageHeader}>
        <div className={styles.headerOverlay}></div>
        <div className={`container ${styles.headerContent}`}>
          <h1>Our Story & Team</h1>
          <p>We believe China deserves to be experienced, not just visited.</p>
        </div>
      </header>

      <main>
        {/* About Info */}
        <section className="section-padding">
          <div className={`container ${styles.aboutGrid}`}>
            <div className="fade-in">
              <img src="/images/office/office-meeting-2.png" alt="Team meeting" className={styles.aboutImage} />
            </div>
            <div className={`fade-in ${styles.aboutText}`}>
              <p className={styles.eyebrow}>Who We Are</p>
              <h2>Your Private China Trip Designers</h2>
              <p>At Orient Surprises Travel, we design private China journeys for international guests who want more than a standard itinerary. We combine local knowledge, cultural understanding, practical planning, and real-time support.</p>
              <p>Many of our team members have studied, worked, or built lives abroad. We understand both Chinese travel operations and the expectations of international guests.</p>
              <blockquote>
                "China has been sharing its stories for five thousand years. We are here to make sure yours begins on the right chapter."
              </blockquote>
            </div>
          </div>
        </section>

        {/* Special Needs & Muslim Friendly */}
        <section className="bg-light section-padding">
          <div className="container">
            <div className="text-center fade-in" style={{ marginBottom: 'var(--space-12)'}}>
              <p className={styles.eyebrow}>Travel Designed Around Your Needs</p>
              <h2>Special Travel Requirements</h2>
              <p>Whether you travel with family, follow religious requirements, or prefer a slower pace.</p>
            </div>
            <div className={styles.needsGrid}>
              <div className={`fade-in ${styles.needsCard}`}>
                <div className={styles.needsIcon}>☪️</div>
                <h3>Muslim & Islamic Travelers</h3>
                <p>We can arrange halal meals, prayer time considerations, halal-friendly restaurants, and suitable hotel suggestions whenever available. We help avoid pork and alcohol-related dining arrangements according to guest preferences.</p>
              </div>
              <div className={`fade-in ${styles.needsCard}`}>
                <div className={styles.needsIcon}>🌿</div>
                <h3>Senior & Retired Travelers</h3>
                <p>For senior and retired travelers, we can design a slower-paced itinerary, reduce unnecessary walking, arrange more comfortable vehicles and hotels, and suggest wheelchair-friendly routes or fewer-stair options.</p>
              </div>
              <div className={`fade-in ${styles.needsCard}`}>
                <div className={styles.needsIcon}>👨‍👩‍👧‍👦</div>
                <h3>Families & Children</h3>
                <p>For families, we can arrange child-friendly attractions, a more relaxed daily schedule, larger vehicles, connecting rooms, and suitable meal suggestions for children.</p>
              </div>
              <div className={`fade-in ${styles.needsCard}`}>
                <div className={styles.needsIcon}>🧳</div>
                <h3>Solo Travelers</h3>
                <p>For solo travelers, we provide safer route suggestions, airport transfers, night-time travel advice, and real-time support during the journey.</p>
              </div>
            </div>
          </div>
        </section>

        {/* Meet the Team (客服介绍) */}
        <section className="section-padding">
          <div className="container">
            <div className="text-center fade-in" style={{ marginBottom: 'var(--space-12)'}}>
              <p className={styles.eyebrow}>Meet the Team</p>
              <h2>Meet Your Private Trip Designers</h2>
              <p>Behind every private journey is a real travel designer who listens, plans, checks, and supports.</p>
            </div>
            <div className={styles.teamGrid}>
              
              <div className={`fade-in ${styles.teamCard}`}>
                <img src="/images/team/Yoyo.jpg" alt="Yoyo" className={styles.teamPhoto} />
                <div className={styles.teamInfo}>
                  <h3>Yoyo</h3>
                  <p className={styles.teamRole}>Private Trip Designer</p>
                  <p>Yoyo works closely with international guests to understand their travel needs and design personalized inbound China itineraries. She coordinates licensed guides, private vehicles, and boutique accommodation.</p>
                </div>
              </div>

              <div className={`fade-in ${styles.teamCard}`}>
                <img src="/images/team/Elaine.jpg" alt="Elaine" className={styles.teamPhoto} />
                <div className={styles.teamInfo}>
                  <h3>Elaine</h3>
                  <p className={styles.teamRole}>Private Trip Designer</p>
                  <p>Elaine responds quickly to travel inquiries and designs personalized China itineraries based on each guest's budget and style. She provides one-stop support for tickets, trains, and hotels.</p>
                </div>
              </div>

              <div className={`fade-in ${styles.teamCard}`}>
                <img src="/images/team/Kiki.jpg" alt="Kiki" className={styles.teamPhoto} />
                <div className={styles.teamInfo}>
                  <h3>Kiki</h3>
                  <p className={styles.teamRole}>Private Trip Designer</p>
                  <p>Kiki designs clear, practical, and easy-to-follow daily itineraries with suggested timing and travel notes. She turns complicated China travel rules into simple, friendly travel guidance.</p>
                </div>
              </div>

            </div>
          </div>
        </section>

      </main>

      <Footer />
    </>
  );
}
