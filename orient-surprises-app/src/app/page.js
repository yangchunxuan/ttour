import Link from "next/link";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import SearchBox from "@/components/SearchBox";
import styles from "./page.module.css";

export default function Home() {
  return (
    <>
      <Navbar />

      <main>
        {/* Hero Section */}
        <section className={styles.hero}>
          <div className={styles.heroOverlay}></div>
          <div className={`container ${styles.heroContent}`}>
            <p className={styles.heroEyebrow}>INTO TRAVEL CHINA</p>
            <h1 className={styles.heroHeadline}>
              Private China Travel,<br />Designed Around You
            </h1>
            <p className={styles.heroSub}>
              Discover China and enjoy a customized trip backed by a full-time team of around 200 travel professionals.
            </p>
            
            <div className={`fade-in ${styles.searchWrap}`}>
              <SearchBox />
            </div>
          </div>
        </section>

        {/* Trust Bar */}
        <section className={styles.trustBar}>
          <div className="container">
            <div className={styles.trustGrid}>
              <div className="fade-in">
                <span className={styles.trustNumber}>200<span>+</span></span>
                <span className={styles.trustLabel}>Professional Guides</span>
              </div>
              <div className="fade-in">
                <span className={styles.trustNumber}>10<span>K+</span></span>
                <span className={styles.trustLabel}>Happy Customers</span>
              </div>
              <div className="fade-in">
                <span className={styles.trustNumber}>15<span>+</span></span>
                <span className={styles.trustLabel}>Years Experience</span>
              </div>
              <div className="fade-in">
                <span className={styles.trustNumber}>500<span>+</span></span>
                <span className={styles.trustLabel}>Tours Completed</span>
              </div>
            </div>
          </div>
        </section>

        {/* Top Destinations */}
        <section className="bg-light section-padding">
          <div className="container">
            <div className="text-center fade-in">
              <h2>China's Top Tourist Cities</h2>
              <p>We proudly recommend the most popular destinations for an unforgettable experience.</p>
            </div>
            
            <div className={styles.destGrid}>
              <Link href="/tours?city=beijing" className={`fade-in ${styles.destCard}`}>
                <img src="https://images.unsplash.com/photo-1508804185872-d7badad00f7d?w=800&q=80" alt="Beijing" />
                <div className={styles.destOverlay}>
                  <h3>Beijing</h3>
                  <p>Imperial Heritage</p>
                </div>
              </Link>
              <Link href="/tours?city=shanghai" className={`fade-in ${styles.destCard}`}>
                <img src="https://images.unsplash.com/photo-1537531383496-b57e4c08bf71?w=800&q=80" alt="Shanghai" />
                <div className={styles.destOverlay}>
                  <h3>Shanghai</h3>
                  <p>Modern Metropolis</p>
                </div>
              </Link>
              <Link href="/tours?city=zhangjiajie" className={`fade-in ${styles.destCard}`}>
                <img src="https://images.unsplash.com/photo-1531366936337-7c912a4589a7?w=800&q=80" alt="Zhangjiajie" />
                <div className={styles.destOverlay}>
                  <h3>Zhangjiajie</h3>
                  <p>Avatar Mountains</p>
                </div>
              </Link>
              <Link href="/tours?city=chengdu" className={`fade-in ${styles.destCard}`}>
                <img src="https://images.unsplash.com/photo-1564760055775-d63b17a55c44?w=800&q=80" alt="Chengdu" />
                <div className={styles.destOverlay}>
                  <h3>Chengdu</h3>
                  <p>Panda Hometown</p>
                </div>
              </Link>
            </div>
          </div>
        </section>

        {/* Reviews */}
        <section className="section-padding">
          <div className="container">
            <div className="text-center fade-in">
              <h2>What Our Travelers Say</h2>
              <p>We are honored to have gained the trust of friends from all over the world.</p>
            </div>
            
            <div className={styles.reviewsGrid}>
              <div className={`fade-in ${styles.reviewCard}`}>
                <div className={styles.quoteMark}>"</div>
                <p>Orient Surprises provided clear information and professional solutions. The attentive service from consultation to travel made our trip seamless.</p>
                <div className={styles.author}>
                  <div className={styles.avatar}>SC</div>
                  <div>
                    <strong>Sarah C.</strong><br/>
                    <span>United Kingdom</span>
                  </div>
                </div>
              </div>
              <div className={`fade-in ${styles.reviewCard}`}>
                <div className={styles.quoteMark}>"</div>
                <p>Our customized 10-day tour was incredible. The guide was knowledgeable and very patient with our family. We felt safe and well-taken care of the entire time.</p>
                <div className={styles.author}>
                  <div className={styles.avatar}>MJ</div>
                  <div>
                    <strong>Mark J.</strong><br/>
                    <span>Australia</span>
                  </div>
                </div>
              </div>
              <div className={`fade-in ${styles.reviewCard}`}>
                <div className={styles.quoteMark}>"</div>
                <p>We requested a slower-paced itinerary as senior travelers. The team was extremely accommodating, arranging wheelchair-friendly routes. Highly recommended!</p>
                <div className={styles.author}>
                  <div className={styles.avatar}>EL</div>
                  <div>
                    <strong>Emily L.</strong><br/>
                    <span>USA</span>
                  </div>
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
