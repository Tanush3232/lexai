"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { authApi } from "@/lib/api";
import { useAuthStore } from "@/lib/stores/auth-store";
import { toast } from "sonner";
import { Scale, Loader2 } from "lucide-react";

const schema = z.object({
  full_name: z.string().min(2, "Full name required"),
  email: z.string().email("Invalid email"),
  password: z.string().min(8, "Password must be at least 8 characters"),
  role: z.string(),
});
type Form = z.infer<typeof schema>;

const ROLES = [
  { value: "legal_team", label: "Legal Team" },
  { value: "reviewer", label: "Reviewer / Approver" },
  { value: "ops_admin", label: "Ops Admin" },
];

export default function RegisterPage() {
  const router = useRouter();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [loading, setLoading] = useState(false);

  const { register, handleSubmit, formState: { errors } } = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: { role: "legal_team" },
  });

  const onSubmit = async (data: Form) => {
    setLoading(true);
    try {
      await authApi.register(data);
      const login = await authApi.login(data.email, data.password);
      setAuth(login.data.user, login.data.access_token);
      toast.success("Account created!");
      router.push("/dashboard");
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden"
         style={{ background: "var(--bg-primary)" }}>
      <div className="absolute top-1/4 left-1/4 w-96 h-96 rounded-full opacity-10"
           style={{ background: "radial-gradient(circle, #8b5cf6, transparent)", filter: "blur(80px)" }} />
      <div className="w-full max-w-md mx-4 relative z-10">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl mb-4"
               style={{ background: "linear-gradient(135deg, #7c3aed, #8b5cf6)", boxShadow: "0 0 40px rgba(139,92,246,0.4)" }}>
            <Scale className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-3xl font-bold gradient-text">LexAI</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>Create your account</p>
        </div>

        <div className="glass-card p-8">
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            {[
              { name: "full_name", label: "Full Name", type: "text", placeholder: "Jane Smith" },
              { name: "email", label: "Email Address", type: "email", placeholder: "you@lawfirm.com" },
              { name: "password", label: "Password", type: "password", placeholder: "Min. 8 characters" },
            ].map(({ name, label, type, placeholder }) => (
              <div key={name}>
                <label className="block text-sm font-medium mb-2" style={{ color: "var(--text-secondary)" }}>{label}</label>
                <input {...register(name as keyof Form)} type={type} placeholder={placeholder} className="input-field" />
                {errors[name as keyof Form] && (
                  <p className="text-xs mt-1.5" style={{ color: "var(--danger)" }}>
                    {errors[name as keyof Form]?.message}
                  </p>
                )}
              </div>
            ))}

            <div>
              <label className="block text-sm font-medium mb-2" style={{ color: "var(--text-secondary)" }}>Role</label>
              <select {...register("role")} className="input-field">
                {ROLES.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
              </select>
            </div>

            <button type="submit" disabled={loading} className="btn-primary w-full flex items-center justify-center gap-2 mt-2">
              {loading ? <><Loader2 className="w-4 h-4 animate-spin" /> Creating account...</> : "Create Account"}
            </button>
          </form>

          <div className="mt-6 pt-6 text-center"
               style={{ borderTop: "1px solid var(--border)", color: "var(--text-muted)", fontSize: "13px" }}>
            Already have an account?{" "}
            <a href="/login" style={{ color: "var(--purple-light)" }} className="hover:underline font-medium">Sign in</a>
          </div>
        </div>
      </div>
    </div>
  );
}
