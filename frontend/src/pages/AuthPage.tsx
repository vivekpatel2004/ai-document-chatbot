import type { FormEvent } from 'react';
import { FileIcon, SparkIcon } from '../components/Icons';

export interface AuthPageProps {
  mode: 'login' | 'signup';
  setMode: (mode: 'login' | 'signup') => void;
  name: string;
  setName: (name: string) => void;
  email: string;
  setEmail: (email: string) => void;
  password: string;
  setPassword: (password: string) => void;
  error: string;
  busy: boolean;
  onSubmit: (event: FormEvent) => void;
}

export default function AuthPage(props: AuthPageProps) {
  const { mode, setMode, name, setName, email, setEmail, password, setPassword, error, busy, onSubmit } = props;
  return <main className="auth-page">
    <section className="auth-aside">
      <div className="brand"><span className="brand-mark"><FileIcon /></span><span>AI Document Chatbot</span></div>
      <div className="auth-pitch"><span className="eyebrow">YOUR KNOWLEDGE, IN CONTEXT</span><h1>Documents that<br />talk back.</h1><p>Bring your files together and get clear, sourced answers in seconds.</p><div className="auth-proof"><span className="proof-icon"><SparkIcon /></span><span><b>Grounded in your documents</b><small>Every answer points back to its source.</small></span></div></div>
      <small className="aside-foot">A calmer way to work with information</small>
    </section>
    <section className="auth-main"><div className="auth-card">
      <div className="mobile-brand brand"><span className="brand-mark"><FileIcon /></span><span>AI Document Chatbot</span></div>
      <span className="eyebrow">{mode === 'login' ? 'WELCOME BACK' : 'GET STARTED'}</span>
      <h2>{mode === 'login' ? 'Sign in to AI Document Chatbot' : 'Create your account'}</h2>
      <p className="auth-subtitle">{mode === 'login' ? 'Pick up where you left off.' : 'Your documents, organized in one place.'}</p>
      <form onSubmit={onSubmit} className="auth-form">
        {mode === 'signup' && <label>Your name<input autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Alex Morgan" required maxLength={80} /></label>}
        <label>Email address<input autoComplete="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" required /></label>
        <label>Password<input autoComplete={mode === 'login' ? 'current-password' : 'new-password'} type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="At least 8 characters" minLength={mode === 'signup' ? 8 : 1} required /></label>
        {error && <div className="form-error" role="alert">{error}</div>}
        <button className="primary-action full" disabled={busy}>{busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}<span>→</span></button>
      </form>
      <p className="auth-switch">{mode === 'login' ? 'New here?' : 'Already have an account?'} <button onClick={() => setMode(mode === 'login' ? 'signup' : 'login')}>{mode === 'login' ? 'Create an account' : 'Sign in'}</button></p>
      <p className="privacy-note">Your documents are private to your account.</p>
    </div></section>
  </main>;
}
