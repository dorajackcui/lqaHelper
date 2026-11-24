import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from "@/components/ui/empty";
import { Kbd, KbdGroup } from "@/components/ui/kbd";

export function CloseButton({ id, label = "关闭" }) {
  return <DialogClose asChild><Button type="button" variant="ghost" size="icon" id={id} data-close aria-label={label}><X /></Button></DialogClose>;
}

export function Keycaps({ value }) {
  return <KbdGroup>{(value || "F8").split("+").map((key, i) => <Kbd key={i}>{key}</Kbd>)}</KbdGroup>;
}

export function EmptyState({ icon: Icon, title, children, action }) {
  return <Empty className="empty-state">
    <EmptyHeader><EmptyMedia variant="icon"><Icon /></EmptyMedia>
      <EmptyTitle>{title}</EmptyTitle><EmptyDescription>{children}</EmptyDescription></EmptyHeader>
    {action}
  </Empty>;
}
