"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertIcon, HourglassIcon, LockIcon, TrophyIcon } from "@/components/icons";
import styles from "./page.module.css";

export default function LoginPage() {
  const router = useRouter();
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setEnviando(true);
    setErro(null);
    try {
      const resp = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ senha }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        setErro(data.error ?? "Senha incorreta.");
        return;
      }
      router.replace("/");
      router.refresh();
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className={styles.container}>
      <form className={styles.card} onSubmit={handleSubmit}>
        <div className={styles.marca}>
          <TrophyIcon color="var(--accent)" size={20} />
        </div>
        <div className={styles.titulo}>Cansadão Apostas</div>
        <div className={styles.subtitulo}>Entre com sua senha para continuar</div>

        {erro ? (
          <div className={styles.erro}>
            <AlertIcon size={14} />
            {erro}
          </div>
        ) : null}

        <label className={styles.label} htmlFor="senha">
          Senha
        </label>
        <div className={styles.inputWrapper}>
          <span className={styles.inputIcon}>
            <LockIcon size={15} />
          </span>
          <input
            id="senha"
            className={styles.input}
            type="password"
            placeholder="••••••••"
            value={senha}
            onChange={(e) => setSenha(e.target.value)}
            autoFocus
          />
        </div>

        <button className={styles.button} type="submit" disabled={enviando || !senha}>
          {enviando ? (
            <>
              <span className={styles.spinner}>
                <HourglassIcon size={15} color="#0d0f11" />
              </span>
              Entrando...
            </>
          ) : (
            "Entrar"
          )}
        </button>
      </form>
    </div>
  );
}
