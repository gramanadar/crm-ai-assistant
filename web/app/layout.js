"use client";

import "./globals.css";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ROLES, currentRole } from "./api";

const LINKS = [
  ["/", "Ask"],
  ["/assistant", "Assistant"],
  ["/review", "Review"],
  ["/approvals", "Approvals"],
  ["/documents", "Documents"],
  ["/usage", "Usage"],
];

export default function RootLayout({ children }) {
  const pathname = usePathname();
  const [role, setRole] = useState("user");
  useEffect(() => {
    setRole(currentRole().id);
  }, []);
  function choose(next) {
    localStorage.setItem("token", next.token);
    setRole(next.id);
  }
  return (
    <html lang="en">
      <body>
        <header className="top">
          <div>
            <strong>Document & CRM Assistant</strong>
          </div>
          <nav>
            {LINKS.map(([href, label]) => (
              <Link key={href} href={href} className={pathname === href ? "active" : ""}>
                {label}
              </Link>
            ))}
          </nav>
          <div className="row roles">
            {ROLES.map((item) => (
              <button key={item.id} className={role === item.id ? "on" : ""} onClick={() => choose(item)} type="button">
                {item.id}
              </button>
            ))}
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
