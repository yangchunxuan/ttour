import Link from "next/link";
import styles from "./Footer.module.css";

export default function Footer() {
  return (
    <footer className={styles.footer}>
      <div className="container">
        <div className={styles.footerTop}>
          <div className={styles.footerBrand}>
            <img src="/images/logo/报价单模版_logo.png" alt="Logo" className={styles.footerLogo} />
            <p>Specializing in China Inbound & Asia Travel. Clear pricing without premium, unlocking amazing travel for you.</p>
          </div>
          <div className={styles.footerLinks}>
            <h4>China Cities</h4>
            <ul>
              <li><Link href="/tours?city=beijing">Beijing</Link></li>
              <li><Link href="/tours?city=xian">Xi'an</Link></li>
              <li><Link href="/tours?city=shanghai">Shanghai</Link></li>
              <li><Link href="/tours?city=chengdu">Chengdu</Link></li>
            </ul>
          </div>
          <div className={styles.footerLinks}>
            <h4>Company</h4>
            <ul>
              <li><Link href="/about">About Us</Link></li>
              <li><Link href="/tours">Group Tours</Link></li>
              <li><Link href="/about#team">Our Team</Link></li>
              <li><Link href="#">Reviews</Link></li>
            </ul>
          </div>
          <div className={styles.footerContact}>
            <h4>Contact Us</h4>
            <p><strong>Tel / WhatsApp:</strong> <a href="tel:+8618074445660">+86 180 7444 5660</a></p>
            <p><strong>WeChat:</strong> +86 189 7444 4491</p>
            <p><strong>Email:</strong> <a href="mailto:penny@orientsurprisestravel.cn">penny@orientsurprisestravel.cn</a></p>
            <p><strong>Address:</strong> Room 5S02, Bofu International Cultural Plaza, Zhangjiajie, Hunan</p>
          </div>
        </div>
        <div className={styles.footerBottom}>
          <p>&copy; 2026-2031 Orient Surprises Travel. All rights reserved.</p>
          <div className={styles.footerLegal}>
            <Link href="#">Terms of Service</Link>
            <Link href="#">Privacy Policy</Link>
          </div>
        </div>
      </div>
    </footer>
  );
}
