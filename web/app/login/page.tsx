"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { AlertIcon, HourglassIcon, LockIcon } from "@/components/icons";
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
        {/* Lensing de borda, como nos cards do painel — é o que dá a
            leitura de peça de vidro em vez de retângulo translúcido. */}
        <span className={styles.lensing} aria-hidden="true" />

        {/* A foto do tipster no lugar do troféu genérico: é a marca real
            do grupo, e a mesma imagem que virou o ícone da aba. */}
        <div className={styles.marca}>
          {/* unoptimized: o otimizador do Next recusa a largura que este
              avatar pede (400 em /_next/image?w=96) porque 96 não está
              na lista de tamanhos gerados. Para uma imagem pequena, de
              tamanho fixo e servida do próprio domínio, otimizar não
              traria ganho — só a requisição extra que estava falhando. */}
          <Image
            className={styles.avatar}
            src="/tipster.jpg"
            alt=""
            width={72}
            height={72}
            unoptimized
            priority
          />
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
